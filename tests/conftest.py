# Ajusta o sys.path para incluir a raiz do repositório nos imports dos testes
import os
import sys

repo_root = os.path.dirname(os.path.dirname(__file__))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)