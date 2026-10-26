// calibre-tsnet: an embedded Tailscale node for the Calibre Link plugin.
//
// Built on tsnet from github.com/tailscale/tailscale. The Calibre Link
// plugin (Python, inside calibre) launches this binary when calibre
// starts and kills it when calibre exits. The binary joins Joe's
// tailnet as its OWN device ("calibre-link"), listens on the tailnet,
// and reverse-proxies everything to the plugin's bridge server on
// localhost — which is where the token check and the calibre database
// live. No system-wide Tailscale install is needed on the PC, and the
// node exists only while calibre is running.
//
// Configuration comes from the environment (set by the plugin):
//
//	CLTS_HOSTNAME   tailnet hostname           (default "calibre-link")
//	CLTS_STATE_DIR  tsnet state directory      (default "./calibre-link-tsnet-state")
//	CLTS_AUTHKEY    optional auth key; when empty, first run prints a
//	                one-time login URL instead (see marker lines below)
//	CLTS_LISTEN     tsnet listen address       (default ":8765")
//	CLTS_TARGET     local bridge base URL      (default "http://127.0.0.1:8765")
//
// Marker lines on stdout (the plugin parses these for its status
// dialog and CalibreLink-connection.txt):
//
//	CALIBRE_TSNET auth_url=<url>     login needed — open this URL once
//	CALIBRE_TSNET ip=<ip> dns=<name> node is up on the tailnet
//	CALIBRE_TSNET bridge=ok          local calibre bridge answered /health
package main

import (
	"context"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/signal"
	"regexp"
	"strings"
	"sync"
	"syscall"
	"time"

	"tailscale.com/tsnet"
)

var authURLRe = regexp.MustCompile(`https://login\.tailscale\.com/\S+`)

// findAuthURL extracts a Tailscale login URL from a log line, if any.
// Trailing punctuation from surrounding prose is trimmed.
func findAuthURL(line string) string {
	m := authURLRe.FindString(line)
	return strings.TrimRight(m, `).,;"'`)
}

// authURLReporter emits the CALIBRE_TSNET auth_url marker exactly
// once per distinct URL, whichever source spots it first.
type authURLReporter struct {
	mu   sync.Mutex
	seen string
}

func (r *authURLReporter) report(rawURL string) {
	u := strings.TrimSpace(rawURL)
	if u == "" {
		return
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if u == r.seen {
		return
	}
	r.seen = u
	fmt.Printf("CALIBRE_TSNET auth_url=%s\n", u)
}

func envOr(key, def string) string {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		return v
	}
	return def
}

// newProxy builds the reverse proxy handler that forwards tailnet
// requests to the plugin's localhost bridge, untouched (the bridge
// does its own X-Token auth — the tailnet is the transport, the token
// is still required, defense in depth).
func newProxy(target string) (http.Handler, error) {
	u, err := url.Parse(target)
	if err != nil {
		return nil, fmt.Errorf("bad CLTS_TARGET %q: %w", target, err)
	}
	rp := httputil.NewSingleHostReverseProxy(u)
	orig := rp.Director
	rp.Director = func(r *http.Request) {
		orig(r)
		r.Host = u.Host
	}
	rp.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		log.Printf("proxy error for %s: %v", r.URL.Path, err)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadGateway)
		io.WriteString(w, `{"ok": false, "error": "calibre bridge not reachable on localhost"}`)
	}
	return rp, nil
}

func main() {
	log.SetFlags(log.LstdFlags | log.Lmsgprefix)
	log.SetPrefix("[calibre-tsnet] ")

	hostname := envOr("CLTS_HOSTNAME", "calibre-link")
	stateDir := envOr("CLTS_STATE_DIR", "./calibre-link-tsnet-state")
	listenAddr := envOr("CLTS_LISTEN", ":8765")
	target := envOr("CLTS_TARGET", "http://127.0.0.1:8765")

	if err := os.MkdirAll(stateDir, 0o700); err != nil {
		log.Fatalf("state dir: %v", err)
	}

	reporter := &authURLReporter{}
	// tsnet routes its auth-URL line through UserLogf, not Logf:
	// in tailscale.com v1.104.2 tsnet.go, (*Server).logf checks
	// UserLogf first, while Logf is the verbose backend logger.
	// Setting only Logf (the previous code) missed printAuthURLLoop.
	scanLogf := func(format string, args ...any) {
		line := fmt.Sprintf(format, args...)
		log.Print(line)
		if u := findAuthURL(line); u != "" {
			reporter.report(u)
		}
	}
	srv := &tsnet.Server{
		Hostname: hostname,
		Dir:      stateDir,
		AuthKey:  strings.TrimSpace(os.Getenv("CLTS_AUTHKEY")),
		// Forward tsnet's logs as usual, but also scan them for the
		// login URL — belt and braces alongside the Status poller
		// below, since either may spot it first.
		Logf:     scanLogf,
		UserLogf: scanLogf,
	}
	defer srv.Close()

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	if err := srv.Start(); err != nil {
		log.Fatalf("tsnet start: %v", err)
	}

	// Report node status (and the one-time login URL, when needed)
	// until the node is running on the tailnet.
	go func() {
		lc, err := srv.LocalClient()
		if err != nil {
			log.Printf("local client: %v", err)
			return
		}
		for {
			st, err := lc.Status(ctx)
			if err == nil && st != nil {
				if st.AuthURL != "" {
					reporter.report(st.AuthURL)
				}
				if st.BackendState == "Running" && st.Self != nil {
					ips := st.Self.TailscaleIPs
					ip := ""
					if len(ips) > 0 {
						ip = ips[0].String()
					}
					fmt.Printf("CALIBRE_TSNET ip=%s dns=%s\n",
						ip, strings.TrimSuffix(st.Self.DNSName, "."))
					return
				}
			}
			select {
			case <-ctx.Done():
				return
			case <-time.After(2 * time.Second):
			}
		}
	}()

	// Note (once) when the local calibre bridge starts answering, so
	// the plugin log shows the two halves connected.
	go func() {
		healthURL := strings.TrimSuffix(target, "/") + "/health"
		client := &http.Client{Timeout: 2 * time.Second}
		for {
			resp, err := client.Get(healthURL) //nolint:bodyclose // closed below
			if err == nil {
				io.Copy(io.Discard, resp.Body)
				resp.Body.Close()
				if resp.StatusCode == http.StatusOK {
					fmt.Println("CALIBRE_TSNET bridge=ok")
					return
				}
			}
			select {
			case <-ctx.Done():
				return
			case <-time.After(3 * time.Second):
			}
		}
	}()

	ln, err := srv.Listen("tcp", listenAddr)
	if err != nil {
		log.Fatalf("tsnet listen %s: %v", listenAddr, err)
	}
	handler, err := newProxy(target)
	if err != nil {
		log.Fatalf("%v", err)
	}
	httpSrv := &http.Server{Handler: handler}
	go func() {
		<-ctx.Done()
		httpSrv.Close()
	}()
	log.Printf("serving http://%s%s -> %s (state: %s)", hostname, listenAddr, target, stateDir)
	if err := httpSrv.Serve(ln); err != nil && err != http.ErrServerClosed {
		log.Fatalf("serve: %v", err)
	}
	log.Printf("shut down")
}
