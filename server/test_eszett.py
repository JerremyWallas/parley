# Self-check for the eszett repair logic. Run: python test_eszett.py
# Stubs faster_whisper so the module imports without GPU deps.
import sys
import types

sys.modules.setdefault("faster_whisper", types.SimpleNamespace(WhisperModel=object))

from transcriber import _fix_eszett


def test():
    # known words get repaired
    assert _fix_eszett("Der Massstab auf der Strasse ist gross.") == "Der Maßstab auf der Straße ist groß."
    assert _fix_eszett("Süsse Grüsse aus Giessen") == "Süße Grüße aus Gießen"
    # legitimate double-s stays untouched
    assert _fix_eszett("Das Wasser muss in das Fass, dass es passt.") == "Das Wasser muss in das Fass, dass es passt."
    # all-caps uses SS in German -> untouched
    assert _fix_eszett("GROSS DRAUSSEN") == "GROSS DRAUSSEN"
    # sentence-initial capital preserved
    assert _fix_eszett("Strasse frei.") == "Straße frei."
    print("eszett ok")


if __name__ == "__main__":
    test()
