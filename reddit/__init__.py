from calibre.customize import InterfaceActionBase


class RedditStoryFollower(InterfaceActionBase):
    name = "Reddit Story Follower"
    description = "Follow story series on Reddit (such as r/HFY) as EPUB books that gain new chapters automatically"
    supported_platforms = ["windows", "osx", "linux"]
    author = "Bookfixer"
    version = (1, 7, 1)
    minimum_calibre_version = (6, 0, 0)
    actual_plugin = "calibre_plugins.reddit_follower.action:FollowerAction"
