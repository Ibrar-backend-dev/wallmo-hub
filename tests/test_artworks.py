"""Asserts GET /v1/artworks and GET /v1/categories/{id}/artworks match the
contract: file_path is an absolute CDN URL, created_at is naive UTC ISO,
ordering is created_at DESC, has_next paginates correctly.
"""
