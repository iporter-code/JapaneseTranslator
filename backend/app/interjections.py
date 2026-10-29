"""Exact recognized interjections only; never infer sounds from silence/RMS."""
import unicodedata

INTERJECTIONS = {
    'うん': '嗯。', 'うーん': '嗯……', 'ん': '嗯。', 'あ': '啊。', 'ああ': '啊。',
    'あっ': '啊！', 'え': '诶？', 'えっ': '诶？', 'ええ': '嗯。', 'おお': '哦。', 'おっ': '哦！',
}


def interjection_translation(text):
    normalized = unicodedata.normalize('NFKC', text).strip()
    normalized = ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in normalized)
    key = ''.join(c for c in normalized if not unicodedata.category(c).startswith(('P', 'Z')))
    return INTERJECTIONS.get(key)
