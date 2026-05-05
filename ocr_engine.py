"""
OCR engine module - RapidOCR (ONNX Runtime) text recognition.
Lightweight, no PaddlePaddle dependency, fast inference.
"""
import os
import re
import time
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class OCREngine:
    """RapidOCR engine: ONNX Runtime backend, lightweight and fast."""

    OPTION_PATTERNS: Dict[str, List[str]] = {
        'A': [
            r'A[．、.、:：]\s*(.+?)(?=(?:[B-D][．、.、:：]|\Z))',
            r'A[）)]\s*(.+?)(?=(?:[B-D][）)]|\Z))',
            r'A\s{2,}(.+?)(?=(?:[B-D]\s{2,}|\Z))',
            r'①\s*[Aa]\.?\s*(.+?)(?=(?:②\s*[Bb]|\Z))',
        ],
        'B': [
            r'B[．、.、:：]\s*(.+?)(?=(?:[C-D][．、.、:：]|\Z))',
            r'B[）)]\s*(.+?)(?=(?:[C-D][）)]|\Z))',
            r'B\s{2,}(.+?)(?=(?:[C-D]\s{2,}|\Z))',
            r'②\s*[Bb]\.?\s*(.+?)(?=(?:③\s*[Cc]|\Z))',
        ],
        'C': [
            r'C[．、.、:：]\s*(.+?)(?=(?:D[．、.、:：]|\Z))',
            r'C[）)]\s*(.+?)(?=(?:D[）)]|\Z))',
            r'C\s{2,}(.+?)(?=(?:D\s{2,}|\Z))',
            r'③\s*[Cc]\.?\s*(.+?)(?=(?:④\s*[Dd]|\Z))',
        ],
        'D': [
            r'D[．、.、:：]\s*(.+?)(?=\Z)',
            r'D[）)]\s*(.+?)(?=\Z)',
            r'D\s{2,}(.+?)(?=\Z)',
            r'④\s*[Dd]\.?\s*(.+?)(?=\Z)',
        ]
    }

    QUESTION_CLEAN_PATTERNS: List[Tuple[str, str]] = [
        (r'^\d+[\.\u3001\s]*', ''),
        (r'^[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e]+[\.\u3001\s]*', ''),
        (r'^(\u9898\u76ee|\u95ee\u9898|\u8bf7\u56de\u7b54)'
         r'[\uff1a:]?\s*', ''),
        (r'^\s*', ''),
        (r'\s*$', ''),
    ]

    def __init__(self, use_gpu: bool = False, lang: str = 'ch',
                 show_log: bool = True) -> None:
        self.ocr: Any = None
        self.use_gpu = use_gpu
        self.lang = lang
        self._show_log = show_log
        if not show_log:
            logging.getLogger('rapidocr_onnxruntime').setLevel(logging.WARNING)
        self._init_model()

    def _init_model(self) -> None:
        try:
            logger.info("Loading RapidOCR [ONNX Runtime]...")
            from rapidocr_onnxruntime import RapidOCR  # pylint: disable=import-outside-toplevel

            self.ocr = RapidOCR()

            # Warm-up with a blank image
            warm = np.zeros((60, 120, 3), dtype=np.uint8)
            warm[:] = 255
            _ = self.ocr(warm)
            logger.info("OCR model warm-up completed")

            logger.info("RapidOCR loaded successfully")
        except ImportError as exc:
            logger.error("RapidOCR import failed: %s", exc)
            raise ImportError(
                "pip install rapidocr_onnxruntime"
            ) from exc
        except Exception as exc:
            logger.error("RapidOCR init failed: %s", exc)
            raise RuntimeError(
                "OCR engine init failed: %s" % exc
            ) from exc

    def recognize(self, image: Any) -> Dict[str, Any]:
        try:
            img_array = self._prepare_image(image)
            if img_array is None:
                return self._empty_result("unsupported image type")

            t0 = time.time()
            result, _ = self.ocr(img_array)
            t1 = time.time()

            if not result:
                logger.info("OCR: %.2fs -> no text", t1 - t0)
                return self._empty_result("no text detected")

            texts, confidences = self._extract_texts(result)
            t2 = time.time()

            if not texts:
                logger.info("OCR: %.2fs extract: %.2fs -> low confidence",
                            t1 - t0, t2 - t1)
                return self._empty_result("text confidence too low")

            full_text = '\n'.join(texts)
            avg_confidence = sum(confidences) / len(confidences)

            parsed = self._parse_question(full_text)
            t3 = time.time()
            parsed["confidence"] = avg_confidence
            logger.info(
                "OCR: inference=%.2fs extract=%.2fs parse=%.3fs | "
                "%d lines conf=%.2f",
                t1 - t0, t2 - t1, t3 - t2,
                len(texts), avg_confidence
            )
            return parsed
        except Exception as exc:
            logger.error("OCR recognition error: %s", exc)
            return self._empty_result(str(exc))

    def _prepare_image(self, image: Any) -> Optional[np.ndarray]:
        try:
            if isinstance(image, Image.Image):
                img_array = np.array(image)
            elif isinstance(image, np.ndarray):
                img_array = image
            else:
                return None
            if len(img_array.shape) == 2:
                img_array = np.stack([img_array] * 3, axis=-1)
            elif img_array.shape[-1] == 4:
                img_array = img_array[:, :, :3]
            return img_array
        except Exception as exc:
            logger.error("Image conversion failed: %s", exc)
            return None

    def _extract_texts(self, ocr_result: Any
                       ) -> Tuple[List[str], List[float]]:
        """
        RapidOCR returns a list of lists:
        [[box_coords, text, confidence], ...]
        e.g. [[[x1,y1],[x2,y2],[x3,y3],[x4,y4]], "识别文字", 0.95]
        """
        texts: List[str] = []
        confidences: List[float] = []
        if not ocr_result:
            return texts, confidences
        for item in ocr_result:
            # Each item is [box, text, score]
            if isinstance(item, (list, tuple)) and len(item) >= 3:
                text = str(item[1])
                score = float(item[2])
                if score > 0.4:
                    texts.append(text)
                    confidences.append(score)
        return texts, confidences

    def _parse_question(self, text: str) -> Dict[str, Any]:
        if not text:
            return self._empty_result("text is empty")
        text = '\n'.join(
            re.sub(r'[^\S\n]+', ' ', line).strip()
            for line in text.split('\n')
        )
        options: Dict[str, str] = {}
        for letter in ['A', 'B', 'C', 'D']:
            option_content = self._extract_option(text, letter)
            if option_content:
                options[letter] = option_content[:150].strip()
        question = self._extract_question(text, options)
        return {
            "question": question[:300],
            "options": options,
            "raw_text": text,
            "is_valid": len(options) >= 2,
            "confidence": 0.0
        }

    def _extract_option(self, text: str, letter: str) -> Optional[str]:
        patterns = self.OPTION_PATTERNS.get(letter, [])
        for pattern in patterns:
            try:
                match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
                if match:
                    content = match.group(1).strip()
                    if content and len(content) >= 1:
                        return content
            except re.error as exc:
                logger.warning("Regex error: %s", exc)
                continue
        return None

    def _extract_question(self, text: str,
                          options: Dict[str, str]) -> str:
        if not options:
            return text
        first_pos = len(text)
        for letter in options:
            for sep in ['.', '\u3001', ':', '\uff1a', '\uff09', ')', ' ']:
                pattern = "%s[%s]" % (letter, re.escape(sep))
                pos = text.find(pattern)
                if pos != -1 and pos < first_pos:
                    first_pos = pos
        if first_pos < len(text):
            question = text[:first_pos].strip()
        else:
            question = text
        for pat, replacement in self.QUESTION_CLEAN_PATTERNS:
            question = re.sub(pat, replacement, question,
                              flags=re.IGNORECASE)
        return question.strip()

    def _empty_result(self, reason: str) -> Dict[str, Any]:
        return {
            "question": "",
            "options": {},
            "raw_text": "",
            "is_valid": False,
            "confidence": 0.0,
            "error": reason
        }

    def get_version_info(self) -> Dict[str, Any]:
        try:
            import rapidocr_onnxruntime  # pylint: disable=import-outside-toplevel
            return {
                "rapidocr_version": getattr(
                    rapidocr_onnxruntime, '__version__', 'unknown'
                ),
                "ocr_type": "RapidOCR (ONNX Runtime)",
                "language": self.lang,
                "gpu_enabled": self.use_gpu
            }
        except Exception as exc:
            logger.warning("Get version failed: %s", exc)
            return {"error": str(exc)}


if __name__ == "__main__":
    print("=" * 50)
    print("RapidOCR Fast Mode Test")
    print("=" * 50)
    try:
        engine = OCREngine(use_gpu=False, lang='ch')
        info = engine.get_version_info()
        print("\nEngine version: %s" % info)
        test_path = "test_question.png"
        if os.path.exists(test_path):
            print("\nRecognizing: %s" % test_path)
            img = Image.open(test_path)
            result = engine.recognize(img)
            print("\nResult:")
            q_text = str(result['question'])[:50]
            print("  question: %s..." % q_text)
            print("  options: %s" % result['options'])
            print("  valid: %s" % result['is_valid'])
            print("  confidence: %.3f" % result.get('confidence', 0))
        else:
            print("\nTest file not found: %s" % test_path)
    except Exception as exc:
        print("\nTest failed: %s" % exc)
