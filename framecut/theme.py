APP_STYLE = """
* { font-family: 'Segoe UI', 'Microsoft YaHei'; font-size: 13px; color: #E8EAF0; }
QMainWindow, QDialog, QWidget#root { background: #111217; }
QWidget { background: transparent; }
QMenuBar { background: #16171D; border-bottom: 1px solid #292B34; padding: 3px; }
QMenuBar::item { padding: 6px 11px; border-radius: 5px; }
QMenuBar::item:selected, QMenu::item:selected { background: #2B6D73; }
QMenu { background: #202129; border: 1px solid #383A46; padding: 5px; }
QMenu::item { padding: 7px 28px 7px 12px; border-radius: 4px; }
QMenu::separator { height: 1px; background: #343640; margin: 5px; }
QToolBar { background: #17181E; border: none; spacing: 5px; padding: 6px 9px; }
QToolButton, QPushButton { background: #292B34; border: 1px solid #343741; border-radius: 6px; padding: 6px 12px; min-height: 20px; }
QToolButton:hover, QPushButton:hover { background: #363946; border-color: #515564; }
QToolButton:pressed, QPushButton:pressed { background: #20222A; }
QToolButton:disabled, QPushButton:disabled { color: #696D79; background: #202127; }
QPushButton#primary, QToolButton#primary { background: #19A7A0; color: #071413; border: none; font-weight: 600; }
QPushButton#primary:hover, QToolButton#primary:hover { background: #28C0B7; }
QPushButton#danger { background: #47272D; color: #FF9AA8; }
QPushButton#ghost, QToolButton#ghost { background: transparent; border-color: transparent; }
QPushButton#ghost:hover, QToolButton#ghost:hover { background: #2A2C35; }
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: #1D1F26; border: 1px solid #343741; border-radius: 5px; padding: 5px 7px; selection-background-color: #247F80; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: #24AAA4; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #23252D; border: 1px solid #3B3E49; selection-background-color: #286C70; }
QTabWidget::pane { border: 1px solid #2C2E37; background: #17181E; }
QTabBar::tab { background: #17181E; color: #9297A5; padding: 8px 11px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #6DE0D7; border-bottom-color: #27B9B0; }
QTabBar::tab:hover { color: #E8EAF0; }
QListWidget, QTreeWidget, QTableWidget { background: #17181E; border: 1px solid #2C2E37; alternate-background-color: #1B1D23; outline: none; }
QListWidget::item { padding: 6px; border-radius: 4px; }
QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected { background: #25494D; color: #83E8DF; }
QListWidget::item:hover, QTreeWidget::item:hover { background: #262932; }
QSlider::groove:horizontal { height: 4px; background: #343742; border-radius: 2px; }
QSlider::handle:horizontal { background: #D8FFFB; width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; }
QSlider::sub-page:horizontal { background: #22AAA3; border-radius: 2px; }
QScrollBar:vertical { background: #15161B; width: 10px; margin: 0; }
QScrollBar:horizontal { background: #15161B; height: 10px; margin: 0; }
QScrollBar::handle { background: #424652; border-radius: 4px; min-height: 24px; min-width: 24px; }
QScrollBar::handle:hover { background: #606676; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QSplitter::handle { background: #272932; width: 2px; height: 2px; }
QGroupBox { border: 1px solid #30323C; border-radius: 7px; margin-top: 10px; padding-top: 9px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 9px; padding: 0 4px; color: #A8ADBA; }
QLabel#muted, QLabel[muted="true"] { color: #858B9A; }
QLabel#section { color: #F2F4F8; font-size: 15px; font-weight: 600; }
QLabel#timecode { font-family: 'Consolas'; font-size: 18px; color: #D9FFFB; }
QCheckBox { spacing: 7px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #525664; border-radius: 4px; background: #202229; }
QCheckBox::indicator:checked { background: #20AAA3; border-color: #20AAA3; }
QProgressBar { background: #22242B; border: none; border-radius: 5px; height: 10px; text-align: center; }
QProgressBar::chunk { background: #20AAA3; border-radius: 5px; }
QStatusBar { background: #17181E; border-top: 1px solid #292B33; color: #969CA9; }
QToolTip { background: #282B34; color: #F2F4F8; border: 1px solid #494E5B; padding: 5px; }
QDockWidget::title { background: #1D1F26; padding: 7px; }
"""
