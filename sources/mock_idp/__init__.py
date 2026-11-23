"""Mock identity provider: a stand-in for Okta or Entra ID.

It owns *who people are* and *which groups they belong to*. It holds no
documents, yet a change here can change what someone may read everywhere:
removing alice from ``it-employees`` takes away every page shared with that
group without any page changing. That is why identity is a source of its own.
"""
