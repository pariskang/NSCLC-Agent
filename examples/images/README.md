# Sample films for the perception layer

`sample_placeholder.png` is a **synthetic blank image** used only to demonstrate
the wiring of the film-reading (`read` / `run --images`) path with the offline
mock. It contains no medical content — the mock provider cannot read it and
returns empty descriptors on purpose.

To see the perception layer do real work, configure a vision backend (e.g. Poe
→ Gemini) and point `--images` at your own **de-identified** CT/PET-CT/MRI
slices exported to PNG/JPEG:

```bash
python -m nsclc_agent read --images /path/to/deid_slice.png -c config.yaml -p gemini_vision
```

> ⚠️ Do not commit real patient images. Only use de-identified data, and only
> for educational / research purposes. This is not a medical device.
