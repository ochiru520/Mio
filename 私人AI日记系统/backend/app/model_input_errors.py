"""Recognize explicit provider image-capability rejections, not image format failures."""
import re


def image_input_unsupported(status: int, message: str) -> bool:
    # Streaming error events can omit an HTTP error status after opening the SSE stream.
    if status not in {0, 400, 404, 422}:
        return False
    text = message.lower()
    patterns = (
        r'(?:does not|doesn.t|cannot|can.t) support (?:image|vision|multimodal)(?: input|s|\b)',
        r'(?:image|vision|multimodal) (?:inputs? )?(?:(?:is|are) )?(?:not supported|unsupported)',
        r'image_url is only supported by certain models',
        r'not a (?:vision|multimodal) model',
        r'no endpoints found that support (?:image|vision)',
        r'(?:模型|接口|model).*?不支持(?:图片|图像|视觉)',
    )
    return any(re.search(pattern, text) for pattern in patterns)
