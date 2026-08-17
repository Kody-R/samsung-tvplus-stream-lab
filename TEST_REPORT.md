# Samsung TV Plus Stream Lab v0.2.1 Test Report

## Regression fixed

The v0.2.0 XMLTV channel index stopped scanning as soon as it encountered the first `<programme>` after finding at least one channel. That works only for XMLTV documents that declare all channels first.

v0.2.1 removes the early exit and continues streaming through the entire document, clearing programme elements to avoid retaining their bodies in memory.

## Automated validation

- Python compilation: PASS
- Pytest suite: PASS
- Existing source-import tests: PASS
- New interleaved XMLTV channel/programme regression test: PASS
- JavaScript syntax: PASS
- Compose/CasaOS/GitHub Actions YAML parsing: PASS

## Real provider sample validation

Using the supplied Samsung TV Plus M3U and XMLTV samples:

- M3U channels parsed: 514
- XMLTV channel declarations indexed after sanitizing the browser-copied sample: 579
- Exact M3U `tvg-id` values found in XMLTV: 514 / 514
- v0.2.0 behavior reproduced: only 1 XMLTV channel indexed
- v0.2.1 behavior: all 579 XMLTV channels indexed

The browser-copied XML sample contains display-layer text before the `<tv>` root and decoded bare ampersands; those were normalized only for offline regression validation. The application should continue to use the provider's actual XMLTV URL.
