#!/bin/bash
# Startar HRV-data på Mac. Motsvarar "Starta HRV-data.bat" på Windows.
# Allt som laddas ner hamnar i undermappen .verktyg – inget installeras i systemet.

cd "$(dirname "$0")" || exit 1
printf '\033]0;HRV-data\007'

TOOLS="$PWD/.verktyg"
PKG="$TOOLS/paket"
export UV_INSTALL_DIR="$TOOLS/uv"
export UV_NO_MODIFY_PATH=1
export UV_PYTHON_INSTALL_DIR="$TOOLS/python"
export UV_CACHE_DIR="$TOOLS/cache"
export UV_PYTHON_PREFERENCE=only-managed
UV="$UV_INSTALL_DIR/uv"

fail() {
    echo
    echo "  $1"
    echo
    read -n 1 -s -r -p "  Tryck på valfri tangent för att stänga fönstret."
    exit 1
}
NAT="Kunde inte ladda ner det som behövs. Kontrollera internetanslutningen och försök igen."

if [ ! -x "$UV" ]; then
    echo
    echo "  Första starten: laddar ner det som behövs. Det tar 1-3 minuter."
    echo
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null || fail "$NAT"
    [ -x "$UV" ] || fail "$NAT"
fi

"$UV" python install 3.12 --quiet || fail "$NAT"
PY="$("$UV" python find 3.12)" || fail "$NAT"
[ -n "$PY" ] || fail "$NAT"

# Installera paket om de saknas eller om appen har uppdaterats
if ! cmp -s requirements-lock.txt "$TOOLS/installerat.txt"; then
    echo "  Installerar paket ..."
    rm -rf "$PKG"
    "$UV" pip install --python "$PY" --target "$PKG" -r requirements-lock.txt --quiet || fail "$NAT"
    cp requirements-lock.txt "$TOOLS/installerat.txt"
fi

export PYTHONPATH="$PKG"
export PYTHONNOUSERSITE=1
echo "  Startar HRV-data ..."
"$PY" starta.py || fail "Något gick fel. Ta en skärmbild av det här fönstret och skicka till den som gav dig appen."
