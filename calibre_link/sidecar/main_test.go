package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// The proxy must pass requests through untouched (path, headers) so
// the bridge's own X-Token auth sees exactly what the client sent.
func TestProxyPassthrough(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("X-Token"); got != "sekrit" {
			t.Errorf("X-Token not forwarded: %q", got)
		}
		if r.URL.Path != "/books" || r.URL.Query().Get("q") != "earthsea" {
			t.Errorf("path/query mangled: %s", r.URL.String())
		}
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"ok": true}`)
	}))
	defer upstream.Close()

	handler, err := newProxy(upstream.URL)
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest("GET", "http://calibre-link:8765/books?q=earthsea", nil)
	req.Header.Set("X-Token", "sekrit")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	if !strings.Contains(rec.Body.String(), `"ok": true`) {
		t.Fatalf("body = %q", rec.Body.String())
	}
}

// When the calibre bridge is down, callers get a clean JSON 502, not
// a hung connection or an HTML error page.
func TestProxyBadGateway(t *testing.T) {
	handler, err := newProxy("http://127.0.0.1:1") // nothing listens there
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest("GET", "http://calibre-link:8765/health", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusBadGateway {
		t.Fatalf("status = %d, want 502", rec.Code)
	}
	if !strings.Contains(rec.Body.String(), "calibre bridge not reachable") {
		t.Fatalf("body = %q", rec.Body.String())
	}
}

func TestEnvOr(t *testing.T) {
	t.Setenv("CLTS_TEST_X", "  spaced  ")
	if got := envOr("CLTS_TEST_X", "def"); got != "spaced" {
		t.Fatalf("envOr = %q", got)
	}
	if got := envOr("CLTS_TEST_MISSING", "def"); got != "def" {
		t.Fatalf("envOr missing = %q", got)
	}
}

func TestFindAuthURL(t *testing.T) {
	cases := map[string]string{
		"control: auth url is https://login.tailscale.com/a/abc123 ok": "https://login.tailscale.com/a/abc123",
		"To authenticate, visit: https://login.tailscale.com/a/xyz.":   "https://login.tailscale.com/a/xyz",
		"no url here": "",
		"see (https://login.tailscale.com/a/paren)": "https://login.tailscale.com/a/paren",
	}
	for in, want := range cases {
		if got := findAuthURL(in); got != want {
			t.Errorf("findAuthURL(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestAuthURLReporterDedupes(t *testing.T) {
	r := &authURLReporter{}
	r.report("https://login.tailscale.com/a/1")
	r.report("https://login.tailscale.com/a/1") // duplicate: no crash, no-op
	r.report("")                                // empty: no-op
	if r.seen != "https://login.tailscale.com/a/1" {
		t.Fatalf("seen = %q", r.seen)
	}
}
