"""
Emoji Helper & Converter Utility for Telegram Premium Emojis and Antigravity.

Telegram Premium custom emojis have unique 64-bit numerical IDs (custom_emoji_id).
When sending HTML formatted messages in Telegram Bot API:
    <tg-emoji emoji-id="5368324170671202286">🔥</tg-emoji>
When sending MarkdownV2:
    ![🔥](tg://emoji?id=5368324170671202286)

This utility:
1. Formats Telegram Premium Custom Emojis for use in bots and Antigravity.
2. Extracts custom_emoji_id from Telegram MessageEntity objects.
3. Provides CLI tools for inspecting and generating emoji tags.
"""

import sys
import html
import unicodedata
from typing import List, Dict, Any, Optional


def build_custom_emoji_html(custom_emoji_id: str, fallback: str = "⭐️") -> str:
    """Returns HTML tag for Telegram Premium custom emoji."""
    return f'<tg-emoji emoji-id="{custom_emoji_id}">{html.escape(fallback)}</tg-emoji>'


def build_custom_emoji_markdown(custom_emoji_id: str, fallback: str = "⭐️") -> str:
    """Returns MarkdownV2 string for Telegram Premium custom emoji."""
    return f"![{fallback}](tg://emoji?id={custom_emoji_id})"


def extract_custom_emojis_from_message(message) -> List[Dict[str, Any]]:
    """
    Extracts custom emojis from a python-telegram-bot Message object.
    Returns list of dicts with custom_emoji_id, offset, length, and text segment.
    """
    results = []
    if not message:
        return results

    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []

    for entity in entities:
        # Check if entity is custom_emoji
        entity_type = getattr(entity, "type", "")
        # PTB entity type can be string 'custom_emoji' or MessageEntityType.CUSTOM_EMOJI
        if str(entity_type) in ("MessageEntityType.CUSTOM_EMOJI", "custom_emoji"):
            emoji_id = getattr(entity, "custom_emoji_id", None)
            offset = entity.offset
            length = entity.length
            # UTF-16 code units handling (Telegram uses UTF-16 code units for offsets)
            # In standard Python, slice handles unicode code points; convert utf-16 offset:
            try:
                utf16_bytes = text.encode("utf-16-le")
                emoji_bytes = utf16_bytes[offset * 2 : (offset + length) * 2]
                raw_char = emoji_bytes.decode("utf-16-le")
            except Exception:
                raw_char = text[offset : offset + length] if offset < len(text) else "✨"

            results.append({
                "custom_emoji_id": str(emoji_id),
                "character": raw_char,
                "offset": offset,
                "length": length,
                "html_tag": build_custom_emoji_html(str(emoji_id), raw_char),
                "markdown_tag": build_custom_emoji_markdown(str(emoji_id), raw_char),
            })
    return results


def analyze_unicode_text(text: str) -> List[Dict[str, str]]:
    """Analyzes text and detects standard Unicode emoji codepoints."""
    findings = []
    for char in text:
        cat = unicodedata.category(char)
        # So (Symbol, other), Sk (Modifier), Sm (Math), etc.
        if cat in ("So", "Sk") or ord(char) > 0x1F000:
            name = unicodedata.name(char, "UNKNOWN")
            codepoint = f"U+{ord(char):04X}"
            findings.append({
                "char": char,
                "codepoint": codepoint,
                "name": name,
            })
    return findings


if __name__ == "__main__":
    import argparse

    # Ensure stdout handles UTF-8 on Windows
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Telegram Premium Emoji Converter")
    parser.add_argument("--id", type=str, help="Telegram custom_emoji_id")
    parser.add_argument("--fallback", type=str, default="🌟", help="Fallback emoji character")
    parser.add_argument("--text", type=str, help="Inspect standard Unicode text for emojis")

    args = parser.parse_args()

    if args.id:
        print(f"Custom Emoji ID: {args.id}")
        print(f"HTML:       {build_custom_emoji_html(args.id, args.fallback)}")
        print(f"MarkdownV2: {build_custom_emoji_markdown(args.id, args.fallback)}")
        print(f"Python:     f'<tg-emoji emoji-id=\"{args.id}\">{args.fallback}</tg-emoji>'")
    elif args.text:
        results = analyze_unicode_text(args.text)
        print(f"Found {len(results)} Unicode symbol(s):")
        for r in results:
            print(f"  {r['char']}  {r['codepoint']}  {r['name']}")
    else:
        parser.print_help()
