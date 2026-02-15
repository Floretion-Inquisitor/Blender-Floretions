import os
import sys
import site

def bootstrap_sys_path():
    """
    - Aggiunge user site-packages (dove pip ha installato pandas, ecc.)
    - Aggiunge la cartella Cranborg dove vivono floretion.py e lib/
    """
    # 1) user site-packages
    try:
        user_site = site.getusersitepackages()
        if user_site and user_site not in sys.path:
            sys.path.append(user_site)
    except Exception:
        pass

    # 2) percorso Cranborg - puoi cambiare questo o usare una env var
    cranborg_default = r"c:\Users\flore\Desktop\Cranborg"
    cranborg_path = os.environ.get("CRANBORG_PATH", cranborg_default)

    if cranborg_path and cranborg_path not in sys.path:
        sys.path.append(cranborg_path)
