"""Shared Ollama transport for the UI and command-line pipelines."""
import requests


def generate(url: str, payload: dict, timeout: int = 600):
    payload = dict(payload)
    if payload.get("model", "").endswith("-mlx"):
        # Ollama 0.40.0 mlxrunner/speculate.go disables speculative decoding
        # for logprobs requests. draft_num_predict=0 is ignored by this runner.
        # Avoid its cache-offset panic while preserving the selected model.
        payload["logprobs"] = True
    response = requests.post(url, json=payload, timeout=timeout)
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        try:
            detail = response.json().get("error", response.text)
        except ValueError:
            detail = response.text
        raise requests.HTTPError(
            f"{exc} — {str(detail)[:1500]}", response=response
        ) from exc
    return response
