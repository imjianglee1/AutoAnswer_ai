"""
主程序 - 基于RapidOCR的屏幕截图答题辅助工具
改进：完善依赖检查、修复线程安全、更健壮的错误处理
"""

import sys
import os
import json
import time
import logging
from typing import Optional, Dict

# 脚本所在目录，用于定位同目录下的文件
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REGION_JSON = os.path.join(BASE_DIR, "capture_region.json")
REGION_SELECTOR_SCRIPT = os.path.join(BASE_DIR, "region_selector.py")

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def check_dependencies():
    """检查所有必要的依赖"""
    missing = []

    # 核心依赖（RapidOCR替代PaddleOCR）
    core_deps = {
        'PyQt5': 'PyQt5',
        'mss': 'mss',
        'PIL': 'Pillow',
        'keyboard': 'keyboard',
        'rapidocr_onnxruntime': 'rapidocr_onnxruntime',
    }

    for module, package in core_deps.items():
        try:
            __import__(module)
            logger.debug(f"✓ {package}")
        except ImportError:
            missing.append(package)
            logger.warning(f"✗ {package} 未安装")

    if missing:
        print("\n" + "=" * 50)
        print("⚠️  缺少必要的依赖包")
        print("=" * 50)
        print("\n请运行以下命令安装：")
        print(f"  pip install {' '.join(missing)}")
        print("\n或一次性安装所有依赖：")
        print("  pip install PyQt5 mss Pillow keyboard rapidocr_onnxruntime")
        print("=" * 50)

        input("\n按回车退出...")
        sys.exit(1)

    logger.info("✓ 所有依赖检查通过")


# 延迟导入（依赖检查后）
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QThread, pyqtSignal, QObject

from config import (
    CAPTURE_REGION, HOTKEY_SELECT, HOTKEY_PAUSE,
    HOTKEY_CAPTURE, HOTKEY_EXIT, SCAN_INTERVAL,
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
    DEEPSEEK_ENABLE_SEARCH
)
from overlay import AnswerOverlay
from ocr_engine import OCREngine
from ai_solver import DeepSeekSolver


class HotkeyHandler(QObject):
    """处理热键回调，通过信号确保 UI 调用线程安全"""
    select_region_signal = pyqtSignal()
    toggle_pause_signal = pyqtSignal()
    capture_signal = pyqtSignal()
    exit_signal = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

    def setup_hotkeys(self):
        try:
            import keyboard
            keyboard.add_hotkey(HOTKEY_SELECT, lambda: self.select_region_signal.emit())
            keyboard.add_hotkey(HOTKEY_PAUSE, lambda: self.toggle_pause_signal.emit())
            keyboard.add_hotkey(HOTKEY_CAPTURE, lambda: self.capture_signal.emit())
            keyboard.add_hotkey(HOTKEY_EXIT, lambda: self.exit_signal.emit())
            logger.info(f"热键已注册: {HOTKEY_SELECT}, {HOTKEY_PAUSE}, {HOTKEY_CAPTURE}, {HOTKEY_EXIT}")
        except Exception as e:
            logger.error(f"热键注册失败: {e}")


class CaptureThread(QThread):
    """屏幕捕获线程"""
    result_signal = pyqtSignal(dict)
    status_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.running = True
        self.paused = True
        self.single_shot = False
        self.sct = None

        # OCR和AI将在run中初始化
        self.ocr = None
        self.ai = None

        # 加载捕获区域
        self.region = self._load_region()

        # 状态变量
        self.last_text = ""
        self.api_count = 0
        self.hit_count = 0
        self.start_time = time.time()

    def _load_region(self) -> Dict:
        """加载捕获区域配置"""
        try:
            with open(REGION_JSON, "r", encoding="utf-8") as f:
                region = json.load(f)
                logger.info(f"已加载捕获区域: {region}")
                return region
        except FileNotFoundError:
            logger.info(f"使用默认捕获区域: {CAPTURE_REGION}")
            return CAPTURE_REGION
        except Exception as e:
            logger.error(f"加载区域配置失败: {e}")
            return CAPTURE_REGION

    def run(self):
        """线程主循环"""
        try:
            import mss
            self.sct = mss.MSS()

            # 在运行线程中初始化引擎（避免构造时阻塞信号连接）
            self.ocr = self._init_ocr_engine()
            if self.ocr is None:
                logger.error("OCR引擎初始化失败，线程终止")
                return

            self.ai = self._init_ai_solver()
            if self.ai is None:
                logger.error("AI解题器初始化失败，线程终止")
                return

            logger.info(f"""
{'='*50}
🚀 DeepSeek自动答题助手已启动
{'='*50}
捕获区域: {self.region}
快捷键:
  • Ctrl+F1 = 重新框选区域
  • Ctrl+F2 = 暂停/继续
  • Ctrl+F3 = 手动识别一次 ⚡
  • Ctrl+Q  = 退出程序
{'='*50}
""")

            while self.running:
                if self.single_shot:
                    self.single_shot = False
                    try:
                        self._process_frame()
                    except Exception as e:
                        logger.error(f"处理帧时出错: {e}")
                elif not self.paused:
                    try:
                        self._process_frame()
                    except Exception as e:
                        logger.error(f"处理帧时出错: {e}")
                time.sleep(SCAN_INTERVAL)

        except Exception as e:
            logger.error(f"捕获线程异常: {e}")
        finally:
            if self.sct:
                try:
                    self.sct.close()
                except:
                    pass

    def _init_ocr_engine(self) -> Optional[OCREngine]:
        """初始化OCR引擎（可能耗时，放在run中）"""
        try:
            logger.info("正在初始化OCR引擎...")
            engine = OCREngine(use_gpu=False, lang='ch', show_log=False)
            version_info = engine.get_version_info()
            logger.info(f"OCR引擎版本: {version_info}")
            return engine
        except Exception as e:
            logger.error(f"OCR引擎初始化失败: {e}")
            return None

    def _init_ai_solver(self) -> Optional[DeepSeekSolver]:
        """初始化AI解题器"""
        try:
            logger.info("正在初始化AI解题器...")
            solver = DeepSeekSolver(
                api_key=DEEPSEEK_API_KEY,
                base_url=DEEPSEEK_BASE_URL,
                model=DEEPSEEK_MODEL,
                enable_search=DEEPSEEK_ENABLE_SEARCH
            )
            logger.info("AI解题器初始化完成")
            return solver
        except Exception as e:
            logger.error(f"AI解题器初始化失败: {e}")
            return None

    def _process_frame(self):
        """处理单帧"""
        if not self.ocr or not self.ai:
            return

        try:
            t_total = time.time()

            t0 = time.time()
            screenshot = self.sct.grab(self.region)
            from PIL import Image
            img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
            t1 = time.time()

            result = self.ocr.recognize(img)
            t2 = time.time()

            if not result or not result.get('is_valid'):
                logger.info(f"⏱ 截图:{t1-t0:.2f}s OCR:{t2-t1:.2f}s → 无效帧，跳过")
                return

            current = result['raw_text'][:50]
            if current == self.last_text:
                return
            self.last_text = current

            question_preview = result['question'][:60]
            logger.info(f"📝 题目: {question_preview}...")

            t3 = time.time()
            answer = self.ai.solve(result)
            t4 = time.time()

            if answer.get('source') == 'DeepSeek':
                self.api_count += 1
            elif answer.get('source') == '本地库':
                self.hit_count += 1

            answer_letter = answer.get('answer', '?')
            source = answer.get('source', 'unknown')
            detail = answer.get('detail', '')

            elapsed_total = time.time() - t_total
            logger.info(f"⏱ 截图:{t1-t0:.2f}s | OCR:{t2-t1:.2f}s | API:{t4-t3:.2f}s | 总计:{elapsed_total:.2f}s")
            logger.info(f"✅ 答案: 【{answer_letter}】 | 来源: {source} | 耗时: {t4-t3:.2f}s")
            if detail:
                logger.debug(f"   解析: {detail[:50]}")

            self.result_signal.emit(answer)

            runtime = time.time() - self.start_time
            stats = f"运行: {int(runtime)}s | API: {self.api_count}次 | 本地: {self.hit_count}次"
            self.status_signal.emit(stats)

        except Exception as e:
            logger.error(f"处理帧失败: {e}")

    def update_region(self, new_region: Dict):
        """更新捕获区域"""
        self.region = new_region
        self.last_text = ""
        logger.info(f"捕获区域已更新: {new_region}")

    def pause(self) -> bool:
        """切换暂停状态"""
        self.paused = not self.paused
        return self.paused

    def stop(self):
        """停止线程"""
        self.running = False
        logger.info("正在停止捕获线程...")

    def trigger_capture(self):
        """触发一次识别"""
        self.single_shot = True


class AutoAnswerApp:
    """主应用类"""
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setApplicationName("DeepSeek答题助手")

        # 创建悬浮窗
        self.overlay = AnswerOverlay()

        # 创建捕获线程
        self.thread = CaptureThread()
        self.thread.result_signal.connect(self.overlay.show_answer)
        self.thread.status_signal.connect(self.overlay.update_status)

        # 初始化热键处理器（线程安全）
        self.hotkey_handler = HotkeyHandler()
        self.hotkey_handler.select_region_signal.connect(self._on_select_region)
        self.hotkey_handler.toggle_pause_signal.connect(self._on_toggle_pause)
        self.hotkey_handler.capture_signal.connect(self._on_capture)
        self.hotkey_handler.exit_signal.connect(self._on_exit)
        self.hotkey_handler.setup_hotkeys()

        # 启动线程
        self.thread.start()
        self.overlay.update_status("状态：按 Ctrl+F3 开始识别")
        logger.info("应用启动完成")

    def _on_capture(self):
        """手动触发一次识别（主线程）"""
        logger.info("⚡ 手动触发识别...")
        self.overlay.show_message("正在识别...")
        self.thread.trigger_capture()

    def _on_select_region(self):
        """重新框选区域（在主线程执行）"""
        logger.info("触发区域选择...")
        was_paused = self.thread.paused
        self.thread.paused = True

        print("\n" + "=" * 40)
        print("请框选要捕获的屏幕区域...")
        print("=" * 40)

        try:
            import subprocess
            result = subprocess.run(
                [sys.executable, REGION_SELECTOR_SCRIPT],
                timeout=60
            )
            try:
                with open(REGION_JSON, "r", encoding="utf-8") as f:
                    new_region = json.load(f)
                    self.thread.update_region(new_region)
                    self.overlay.show_message("区域已更新 ✓")
            except Exception as e:
                logger.error(f"加载新区域失败: {e}")
                self.overlay.show_message("区域加载失败 ✗")
        except subprocess.TimeoutExpired:
            logger.warning("区域选择超时")
            self.overlay.show_message("选择超时 ✗")
        except Exception as e:
            logger.error(f"区域选择失败: {e}")
            self.overlay.show_message("选择失败 ✗")
        finally:
            if not was_paused:
                self.thread.paused = False

    def _on_toggle_pause(self):
        """切换暂停状态（主线程）"""
        is_paused = self.thread.pause()
        if is_paused:
            status = "已暂停 ⏸ | 按 F3 手动识别"
        else:
            status = "连续扫描中 ▶ | 按 F2 停止"
        self.overlay.show_message(status)
        logger.info(status)

    def _on_exit(self):
        """退出程序（主线程）"""
        print("\n" + "=" * 40)
        print("正在退出...")
        print("=" * 40)

        runtime = time.time() - self.thread.start_time
        logger.info(f"运行时长: {int(runtime)}秒")
        logger.info(f"API调用次数: {self.thread.api_count}")
        logger.info(f"本地库命中: {self.thread.hit_count}")

        self.thread.stop()
        self.thread.wait(3000)
        self.app.quit()

    def run(self):
        """运行应用"""
        logger.info("进入主事件循环...")
        sys.exit(self.app.exec_())


def main():
    print("\n" + "=" * 50)
    print("  DeepSeek 自动答题助手 v2.0")
    print("  基于 RapidOCR + DeepSeek API")
    print("=" * 50 + "\n")

    # 检查依赖
    check_dependencies()

    # 检查API密钥
    if not DEEPSEEK_API_KEY or DEEPSEEK_API_KEY == "sk-your-key-here":
        print("\n" + "=" * 50)
        print("❌ 错误：未配置DeepSeek API密钥")
        print("=" * 50)
        print("\n请在环境变量中设置 DEEPSEEK_API_KEY")
        print("Windows: set DEEPSEEK_API_KEY=你的密钥")
        print("Linux/Mac: export DEEPSEEK_API_KEY=你的密钥")
        print("获取地址：https://platform.deepseek.com/")
        print("=" * 50)
        input("\n按回车退出...")
        sys.exit(1)

    try:
        app = AutoAnswerApp()
        app.run()
    except KeyboardInterrupt:
        logger.info("收到键盘中断，正在退出...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"应用异常退出: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
