"""Backblaze B2 (S3-compatible) client. Upload path only - reads never hit B2.

  build_key(category_slug, kind, ext)  -> wallpaper/{slug}/{kind}/{yyyy}/{mm}/{uuid}.{ext}
  build_url(storage_key)               -> f"{MEDIA_BASE_URL}/{storage_key}"
  put_object(key, data, content_type)  -> aioboto3
  make_renditions(file)                -> Pillow: 400w webp, 1080w webp,
                                          dimensions, dominant colour
"""
