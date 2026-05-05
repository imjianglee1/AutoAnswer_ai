"""
AI解题模块 - 使用DeepSeek API进行答案推理
改进：添加重试机制、更完善的错误处理、更健壮的解析
"""

import re
import time
import logging
from typing import Dict, Optional

import openai

logger = logging.getLogger(__name__)


class DeepSeekSolver:
    """DeepSeek API解题器"""

    MAX_RETRIES = 3
    RETRY_DELAY = 1
    TIMEOUT = 15

    def __init__(self, api_key: str, base_url: str, model: str, enable_search: bool = False):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.enable_search = enable_search

        self.client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url
        )

        # 预先导入本地知识库（避免每次调用时动态导入）
        try:
            from config import LOCAL_KNOWLEDGE
            self.local_knowledge = LOCAL_KNOWLEDGE
        except ImportError:
            logger.warning("无法导入本地知识库，将跳过本地查询")
            self.local_knowledge = {}

        logger.info(f"DeepSeek解题器初始化完成，使用模型: {model}，联网搜索: {enable_search}")

    @classmethod
    def from_config(cls, config: Dict):
        """从配置字典创建实例"""
        return cls(
            api_key=config.get('DEEPSEEK_API_KEY', ''),
            base_url=config.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1'),
            model=config.get('DEEPSEEK_MODEL', 'deepseek-chat'),
            enable_search=config.get('DEEPSEEK_ENABLE_SEARCH', False)
        )

    def solve(self, question_data: Dict) -> Dict:
        """解题主入口"""
        if not question_data or not question_data.get('is_valid'):
            return {
                "answer": "?",
                "detail": "未识别题目",
                "source": "error"
            }

        question = question_data['question']
        options = question_data.get('options', {})

        if not question:
            return {
                "answer": "?",
                "detail": "题目为空",
                "source": "error"
            }

        logger.debug(f"开始解题: {question[:50]}...")

        # 第1步：查本地知识库
        t0 = time.time()
        local_result = self._check_local(question)
        t1 = time.time()
        if local_result:
            logger.info(f"⏱ AI本地库命中: {t1-t0:.3f}s")
            logger.debug("命中本地知识库")
            return {
                **local_result,
                "source": "本地库"
            }

        # 第2步：调用DeepSeek API（带重试）
        api_result = self._call_api_with_retry(question, options)
        t2 = time.time()
        logger.info(f"⏱ AI本地库:{t1-t0:.3f}s API+重试:{t2-t1:.2f}s")
        return api_result

    def _check_local(self, question_text: str) -> Optional[Dict]:
        """在本地知识库中查找匹配"""
        if not self.local_knowledge:
            return None

        for item_key, item_data in self.local_knowledge.items():
            keys = item_data.get('keys', [])
            matched_count = sum(1 for key in keys if key in question_text)
            if matched_count >= 1:
                logger.debug(f"本地库匹配: {item_key} ({matched_count}/{len(keys)} 关键词)")
                return {
                    "answer": item_data.get('answer', '?'),
                    "detail": item_data.get('detail', '')
                }
        return None

    def _call_api_with_retry(self, question: str, options: Dict) -> Dict:
        """调用API，带重试机制"""
        last_error = None

        for attempt in range(self.MAX_RETRIES):
            try:
                t0 = time.time()
                result = self._call_api(question, options)
                t1 = time.time()
                logger.info(f"⏱ API单次调用: {t1-t0:.2f}s (尝试{attempt+1}/{self.MAX_RETRIES})")
                if result.get('source') != 'api_error':
                    return result
                last_error = result.get('detail', '未知错误')
            except Exception as e:  # pylint: disable=broad-except
                last_error = str(e)
                logger.warning(f"API调用失败 (尝试 {attempt + 1}/{self.MAX_RETRIES}): {e}")

            if attempt < self.MAX_RETRIES - 1:
                wait_time = self.RETRY_DELAY * (attempt + 1)
                logger.debug(f"等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)

        logger.error(f"API调用全部失败: {last_error}")
        return {
            "answer": "?",
            "detail": f"API失败: {last_error[:30]}",
            "source": "error"
        }

    def _call_api(self, question: str, options: Dict) -> Dict:
        """调用DeepSeek API获取答案"""
        options_text = self._format_options(options)
        prompt = self._build_prompt(question, options_text)

        try:
            logger.debug(f"调用API，问题长度: {len(question)}，联网: {self.enable_search}")

            kwargs = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "你是时政知识专家，只输出答案字母和一句话解析，不要多说。请以最快的速度，只输出答案，无需多次检查，越快越好！！！！"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.0,
                "max_tokens": 20,
                "timeout": self.TIMEOUT
            }

            if self.enable_search:
                kwargs["extra_body"] = {"enable_search": True}

            response = self.client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content.strip()
            logger.debug(f"API响应: {content[:100]}")

            result = self._parse_response(content)

            # 如果启用了联网搜索，检查是否有搜索引用
            if self.enable_search:
                search_info = self._extract_search_citations(response)
                if search_info:
                    result["detail"] = result.get("detail", "") + search_info

            return result

        except openai.APIError as e:
            logger.error(f"OpenAI API错误: {e}")
            raise
        except Exception as e:
            logger.error(f"API调用异常: {e}")
            raise

    def _extract_search_citations(self, response) -> str:
        """从响应中提取联网搜索引用"""
        try:
            citations = getattr(response.choices[0].message, 'search_results', None)
            if citations and len(citations) > 0:
                sources = []
                for c in citations[:3]:
                    title = getattr(c, 'title', '') or getattr(c, 'name', '') or ''
                    url = getattr(c, 'url', '') or ''
                    if title and url:
                        sources.append(f"{title[:30]}: {url[:50]}")
                if sources:
                    return " | 参考: " + "; ".join(sources)
        except Exception:  # pylint: disable=broad-except
            pass
        return ""

    def _format_options(self, options: Dict) -> str:
        if not options:
            return "无选项"
        lines = []
        for key in sorted(options.keys()):
            value = options[key]
            if len(value) > 80:
                value = value[:77] + "..."
            lines.append(f"{key}. {value}")
        return '\n'.join(lines)

    def _build_prompt(self, question: str, options_text: str) -> str:
        return f"""题目：{question}

选项：
{options_text}

请直接回答案字母和一句话解析，越快越好！
格式：答案：X | 解析：XXX
只输出答案，无需多写。"""

    def _parse_response(self, text: str) -> Dict:
        if not text:
            return {"answer": "?", "detail": "空响应", "source": "api_error"}

        answer = "?"
        detail = ""

        # 模式1：标准格式 "答案：X" 或 "答案:X"
        answer_match = re.search(r'答案[：:]\s*([A-Da-d])', text, re.IGNORECASE)
        # 模式2：只有单个字母
        if not answer_match:
            single_letter = re.match(r'^([A-Da-d])\s*[．、.。]?', text.strip())
            if single_letter:
                answer_match = single_letter
        # 模式3："答案是A" 或 "我选B"
        if not answer_match:
            answer_match = re.search(r'(?:答案|选|选择)[是为]?\s*([A-Da-d])', text, re.IGNORECASE)

        if answer_match:
            answer = answer_match.group(1).upper()

        # 提取解析
        detail_patterns = [
            r'解析[：:]\s*(.+?)(?:\n|$)',
            r'原因[：:]\s*(.+?)(?:\n|$)',
            r'因为[：:]\s*(.+?)(?:\n|$)',
        ]
        for pattern in detail_patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                detail = match.group(1).strip()
                break

        if not detail:
            colon_match = re.search(r'[：:](.+?)(?:\n|$)', text)
            if colon_match:
                potential = colon_match.group(1).strip()
                if potential and potential.upper() not in ['A', 'B', 'C', 'D']:
                    detail = potential

        detail = detail[:50] if detail else ""

        return {
            "answer": answer,
            "detail": detail,
            "source": "DeepSeek"
        }


if __name__ == "__main__":
    print("=" * 50)
    print("DeepSeek解题器测试")
    print("=" * 50)

    test_question = {
        "question": "2025年联合国气候变化大会(COP30)在哪举办？",
        "options": {
            "A": "日内瓦",
            "B": "纽约",
            "C": "里约热内卢",
            "D": "贝伦"
        },
        "is_valid": True
    }

    try:
        from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
        solver = DeepSeekSolver(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            model=DEEPSEEK_MODEL
        )
        print("\n测试解题...")
        result = solver.solve(test_question)
        print("\n结果:")
        print(f"  答案: {result['answer']}")
        print(f"  解析: {result['detail']}")
        print(f"  来源: {result['source']}")
    except Exception as e:  # pylint: disable=broad-except
        print(f"\n❌ 测试失败: {e}")