import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.knowledge import get_knowledge

if __name__ == "__main__":
    index = get_knowledge()
    index.ensure(force=True)
    print(f"Indexed {len(index.chunks)} clauses/paragraphs into {index.index_path}")
