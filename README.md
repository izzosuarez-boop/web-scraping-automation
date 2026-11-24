# End-to-end web scraping: from data to decisions

**Portfolio:** [ivanboot.dev](https://ivanboot.dev) · see it working in [Web Scraping, Software Automation & Data](https://ivanboot.dev/automation)

The value of web scraping isn't in collecting data but in using it well: wiring it into scripts and bots that act on
the client's own rules.

> A real client case. The buyer site, the game, its servers and the site's form fields have been replaced with
> generic names; personal data, sessions and sales logs never leave the client's machine.

## The problem

Many decisions depend on information that changes by the minute, and one person can't watch it all day. This case
is selling in-game currency, but the same pattern fits trading, online groceries, airlines, hotel search, real
estate and job boards: manual work that becomes far more profitable once automated. It only makes sense when the
platform has no API; if it has one, the right job is an API integration.

## How I built it

Python throughout. Prices are read without a browser: the page ships its data as embedded JSON, so reading is fast
and light. Playwright drives a real Chrome only to sell, picking the server and filling in the form. A desktop
interface (Tkinter) runs price reading and the browser on separate threads so it never freezes. Data goes to CSV
and JSON, or to Excel or a SQL or NoSQL database if the client needs it. I also work in Node.js and C# when a
project calls for it.

## Result

The client sets a minimum price and an amount. The software checks 7 servers across 3 games every minute, decides
when to sell, prepares the sale and alerts the client, who just confirms. Every step is logged, and when something
goes wrong (an expired session, a server that stops buying) it reports it instead of failing silently.

## What's in this repo

| File | What it does |
|---|---|
| `lector_precios.py` | Reads the prices without a browser, from the JSON the page embeds |
| `vendedor.py` | A real Chrome (Playwright) with a saved session: picks the game, server, payment method and amount, fills in the form and listens for the site's answer. The captcha and the final "Sell" click stay with the person |
| `interfaz.pyw` | The desktop app: monitoring, sale details and the sales log |
| `registro.py` | Sales log in `ventas.csv` |
| `tests/` | 11 tests against a simulated copy of the sale form; every external request is blocked |

The code and its comments are in Spanish, as the client uses it.

## Run it

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v     # offline
pythonw interfaz.pyw                        # the app (needs the real site configured in lector_precios.py)
```

## Stack

Python · Playwright · Chrome · Tkinter · Threading · CSV / JSON
