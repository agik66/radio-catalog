# cliamp-catalog

Stavia katalóg rádiových staníc pre appku Cliamp. Obyčajný deterministický
Python skript — **žiadny agent**. Overiteľnosť je celý zmysel tohto komponentu.

## Použitie

    ./build.py --countries SK,CZ,DE --tags rock,jazz --limit 800 --out dist

Výstupy: `catalog.json` (pre appku), `triage_input.json` (pre sondu),
`stats.json` (kvalita v čase).

## Overenie na simulátore

Databáza vie klamať — `lastcheckok` má presnosť ~95 %. Skutočný test je
prehratie cez reálny AVFoundation stack:

    cd ../CliampIOS
    ./scripts/triage.sh ../cliamp-catalog/dist/triage_input.json verified.json 8 20

## Vrstvy

1. **`net.py`** — bezpečnosť. URL sú cudzie dáta, nie konfigurácia:
   blokuje SSRF (loopback, RFC1918, cloud metadata), nepovolené schémy a porty.
2. **`radiobrowser.py`** — klient s DNS discovery mirrorov, rate-limitom
   a povinným User-Agentom.
3. **`genres.py`** — mapovanie voľných viacjazyčných tagov na kanonické žánre.
   Bez toho je "filter podľa žánru" iba fulltext nad chaosom.
   Pokrytie sa meria (`stats.json` → `taxonomy_coverage_pct`); nezaradené
   tagy zoradené podľa početnosti sú návod, čo doplniť ďalej.
4. **`curate.py`** — kvalitatívna brána, deduplikácia, editorská vrstva.

`curated.json` (editorský zoznam) stojí nad všetkým a vždy vyhráva. To je
vrstva, ktorá robí rozdiel medzi kurátorovaným rádiom a výpisom z databázy.
