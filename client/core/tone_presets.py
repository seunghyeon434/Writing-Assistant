TONE_PRESETS = (
    "귀여운 말투",
    "사랑스러운 말투",
    "무뚝뚝한 말투",
    "정중한 말투",
    "신난 말투",
    "기운없는 말투",
)

DEFAULT_TONE_PRESET = "정중한 말투"


def normalize_tone_preset(tone):
    value = str(tone or "").strip()
    return value if value in TONE_PRESETS else DEFAULT_TONE_PRESET
