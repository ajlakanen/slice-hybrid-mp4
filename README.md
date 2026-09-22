# slice-hybrid-mp4

Pilkkoo OBS Studion Hybrid MP4 -tallenteen clipeiksi chapter-merkkien kohdalta.
Chapter-merkin voi lisätä tallennuksen aikana pikanäppäimellä (OBS 30.2+).

## Vaatimukset

- Python 3.8+
- ffmpeg ja ffprobe. Jos ne puuttuvat, skripti tarjoutuu asentamaan ne
  (Windows: winget/choco/scoop, macOS: brew/port, Linux: apt/dnf/pacman/zypper/apk).

## Käyttö

```sh
python slice-hybrid-mp4.py tallenne.mp4              # clipit kansioon tallenne_clipit/
python slice-hybrid-mp4.py tallenne.mp4 --dry-run    # näytä suunnitelma leikkaamatta
python slice-hybrid-mp4.py tallenne.mp4 --reencode   # tarkat leikkauskohdat, hitaampi
```

Kaikki valitsimet: `python slice-hybrid-mp4.py --help`

Jokainen clippi alkaa edellisestä merkistä ja päättyy seuraavaan. Viimeisen merkin
jälkeinen osa tallennetaan omaksi clipikseen (`--no-tail` jättää sen pois).

Oletuksena video kopioidaan uudelleenkoodaamatta, jolloin leikkaus on nopea mutta
clippi alkaa edellisestä avainkehyksestä, eli se voi alkaa muutaman sekunnin etuajassa.
`--reencode` leikkaa tarkasti.

## Lisenssi

[MIT](LICENSE)
