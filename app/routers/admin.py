"""Admin write endpoints, guarded by ADMIN_API_KEY.

  POST   /admin/categories
  PATCH  /admin/categories/{id}
  POST   /admin/artworks        -> multipart upload: validate mime,
                                   make_renditions, parallel B2 puts, one insert
  DELETE /admin/artworks/{id}   -> soft delete (is_active = false)
"""
