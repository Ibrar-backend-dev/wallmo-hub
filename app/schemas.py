"""Pydantic v2 response models. Field order here defines JSON key order.

  Meta          -> page, page_size, total, has_next
  Page[T]       -> data: list[T], meta: Meta
  Category      -> id, name, description, position, artwork_count
  Artwork       -> media_type, is_premium, title, id, category_id,
                   original_filename, file_path, created_at, color_code
                   (+ thumb_path, preview_path)

  Artwork.file_path is built from settings.MEDIA_BASE_URL + storage_key.
  created_at uses a field_serializer emitting naive UTC ISO (no offset),
  matching the existing frontend contract.
"""
