# utils/labels.py
def ko_label(name_ko: str, item_id: str, show_id: bool = True) -> str:
    return f"{name_ko} ({item_id})" if show_id else name_ko
