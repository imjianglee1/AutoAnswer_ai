#!/usr/bin/env python3
"""
屏幕区域选择工具
使用方法：直接运行或从main.py调用
"""

import json
import os
import sys
from PyQt5.QtWidgets import QApplication, QWidget, QLabel
from PyQt5.QtCore import Qt, QRect, QPoint
from PyQt5.QtGui import QPainter, QPen, QColor, QFont

# 脚本所在目录，用于定位同目录下的配置文件
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REGION_JSON = os.path.join(BASE_DIR, "capture_region.json")


class RegionSelector(QWidget):
    """屏幕区域选择器"""

    def __init__(self):
        super().__init__()
        self.start_point = None
        self.end_point = None
        self.is_selecting = False

        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)

        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)

        self._create_hint_label()
        self._create_coordinate_label()
        self.show()

    def _create_hint_label(self):
        self.hint_label = QLabel(self)
        self.hint_label.setFont(QFont("Microsoft YaHei", 14))
        self.hint_label.setStyleSheet("""
            QLabel {
                color: white;
                background-color: rgba(0, 0, 0, 200);
                padding: 12px 25px;
                border-radius: 8px;
                border: 1px solid rgba(0, 255, 136, 150);
            }
        """)
        self.hint_label.setText(
            "🖱️ 按住鼠标左键框选区域\n"
            "⬅️ 按 Enter 确认 | 按 ESC 取消\n"
            "💡 拖动已选区域可微调"
        )
        self.hint_label.adjustSize()
        self.hint_label.move(50, 50)
        self.hint_label.show()

    def _create_coordinate_label(self):
        self.coord_label = QLabel(self)
        self.coord_label.setFont(QFont("Consolas", 12))
        self.coord_label.setStyleSheet("""
            QLabel {
                color: #00FF88;
                background-color: rgba(0, 0, 0, 180);
                padding: 8px 15px;
                border-radius: 5px;
            }
        """)
        self.coord_label.setText("尚未选择区域")
        self.coord_label.adjustSize()
        self.coord_label.hide()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setBrush(QColor(0, 0, 0, 100))
        painter.setPen(Qt.NoPen)
        painter.drawRect(self.rect())

        if self.start_point and self.end_point:
            rect = QRect(self.start_point, self.end_point).normalized()
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.drawRect(rect)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

            painter.setPen(QPen(QColor(0, 255, 136), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)

            corner_size = 10
            painter.setPen(QPen(QColor(0, 255, 136), 3))
            painter.drawLine(rect.topLeft(), rect.topLeft() + QPoint(corner_size, 0))
            painter.drawLine(rect.topLeft(), rect.topLeft() + QPoint(0, corner_size))
            painter.drawLine(rect.topRight(), rect.topRight() + QPoint(-corner_size, 0))
            painter.drawLine(rect.topRight(), rect.topRight() + QPoint(0, corner_size))
            painter.drawLine(rect.bottomLeft(), rect.bottomLeft() + QPoint(corner_size, 0))
            painter.drawLine(rect.bottomLeft(), rect.bottomLeft() + QPoint(0, -corner_size))
            painter.drawLine(rect.bottomRight(), rect.bottomRight() + QPoint(-corner_size, 0))
            painter.drawLine(rect.bottomRight(), rect.bottomRight() + QPoint(0, -corner_size))

            size_text = f"{rect.width()} × {rect.height()}"
            painter.setPen(QPen(QColor(255, 255, 255), 1))
            painter.setFont(QFont("Microsoft YaHei", 10))
            text_rect = QRect(rect.center().x() - 50, rect.center().y() - 15, 100, 30)
            painter.drawRect(text_rect.adjusted(-2, -2, 2, 2))
            painter.drawText(text_rect, Qt.AlignCenter, size_text)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.start_point = event.pos()
            self.end_point = event.pos()
            self.is_selecting = True
            self.coord_label.show()

    def mouseMoveEvent(self, event):
        if self.is_selecting:
            self.end_point = event.pos()
            self._update_coordinate_label(event.pos())  # 修复：传入鼠标位置
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.end_point = event.pos()
            self.is_selecting = False
            self._update_coordinate_label(event.pos())  # 修复
            self.update()

    def _update_coordinate_label(self, mouse_pos: QPoint = None):
        """更新坐标显示，传入鼠标位置作为标签摆放参考"""
        if self.start_point and self.end_point:
            rect = QRect(self.start_point, self.end_point).normalized()
            text = f"X: {rect.x()}  Y: {rect.y()}  W: {rect.width()}  H: {rect.height()}"
            self.coord_label.setText(text)
            self.coord_label.adjustSize()

            if mouse_pos is not None:
                # 跟随鼠标移动
                self.coord_label.move(mouse_pos + QPoint(20, 20))
            else:
                # 默认放在选区右下角
                self.coord_label.move(rect.bottomRight() + QPoint(10, 10))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Return or event.key() == Qt.Key_Space:
            self._save_region()
        elif event.key() == Qt.Key_Escape:
            print("已取消选择")
            QApplication.instance().quit()
        elif event.key() == Qt.Key_C and event.modifiers() == Qt.ControlModifier:
            self._copy_last_region()

    def _save_region(self):
        if not self.start_point or not self.end_point:
            print("请先框选区域")
            return

        rect = QRect(self.start_point, self.end_point).normalized()
        if rect.width() < 10 or rect.height() < 10:
            print("选区太小，请重新选择")
            return

        region = {
            "left": rect.x(),
            "top": rect.y(),
            "width": rect.width(),
            "height": rect.height()
        }

        try:
            with open(REGION_JSON, "w", encoding="utf-8") as f:
                json.dump(region, f, indent=2, ensure_ascii=False)
            print(f"✅ 区域已保存: {region}")
            QApplication.instance().quit()
        except (OSError, IOError, json.JSONDecodeError) as e:
            print(f"❌ 保存失败: {e}")
            QApplication.instance().quit()

    def _copy_last_region(self):
        # 简单实现，可不做
        pass


if __name__ == "__main__":
    app = QApplication(sys.argv)
    selector = RegionSelector()
    sys.exit(app.exec_())