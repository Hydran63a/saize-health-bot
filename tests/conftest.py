import sys
from pathlib import Path

# プロジェクトルートを import パスに追加（tests/ から config 等を読めるようにする）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
