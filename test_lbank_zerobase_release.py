"""Include isolated ZB acceptance tests in the production pre-deploy suite."""
import sys,unittest
from pathlib import Path
ROOT=Path(__file__).parent/'tests'/'zerobase'
sys.path.insert(0,str(ROOT))
def load_tests(loader,tests,pattern):return unittest.TestLoader().discover(str(ROOT),pattern='test_*.py')
