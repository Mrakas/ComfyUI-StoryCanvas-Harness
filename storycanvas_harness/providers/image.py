from __future__ import annotations

import base64
import io
import os
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFont

from ..errors import ProviderError
from ..schemas import ProviderReceipt
from ..utils import sha256_json
from .base import GeneratedFile


class MockImageProvider:
    name = "mock"
    model = "mock-image-v1"

    def generate(self, prompt: str, references: list[Path], destination: Path) -> GeneratedFile:
        request = {
            "prompt": prompt,
            "references": [path.name for path in references],
            "size": "1344x768",
        }
        digest = sha256_json(request)
        color = tuple(int(digest[offset : offset + 2], 16) for offset in (0, 2, 4))
        image = Image.new("RGB", (1344, 768), color)
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=22)
        text = f"StoryCanvas mock\n\n{prompt[:700]}\n\nrefs: {len(references)}\nsha: {digest[:16]}"
        draw.multiline_text((48, 48), text, fill=(255, 255, 255), font=font, spacing=8)
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, format="PNG")
        return GeneratedFile(
            path=destination,
            receipt=ProviderReceipt(
                provider=self.name,
                model=self.model,
                operation="image_generation",
                request_sha256=digest,
            ),
            metadata={"width": 1344, "height": 768, "mock": True},
        )


class OpenAIImageProvider:
    name = "openai"

    def __init__(self, *, model: str | None = None, client: Any | None = None):
        self.model: str = model or os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as error:  # pragma: no cover
                raise ProviderError(
                    "Install the openai package to use OpenAIImageProvider"
                ) from error
            client = OpenAI()
        self.client: Any = client

    def generate(self, prompt: str, references: list[Path], destination: Path) -> GeneratedFile:
        missing = [str(path) for path in references if not path.is_file()]
        if missing:
            raise ProviderError(f"Missing image references: {missing}")
        request_identity = {
            "model": self.model,
            "prompt": prompt,
            "reference_names": [path.name for path in references],
            "size": "1536x1024",
        }
        try:
            if references:
                with ExitStack() as stack:
                    streams = [stack.enter_context(path.open("rb")) for path in references]
                    response = self.client.images.edit(
                        model=self.model,
                        image=streams,
                        prompt=prompt,
                        size="1536x1024",
                    )
            else:
                response = self.client.images.generate(
                    model=self.model,
                    prompt=prompt,
                    size="1536x1024",
                )
            data = getattr(response, "data", None)
            if not data:
                raise ProviderError("OpenAI image response contains no image data")
            item: Any = data[0]
            encoded = getattr(item, "b64_json", None)
            remote_url = getattr(item, "url", None)
            if encoded:
                raw = base64.b64decode(str(encoded))
            elif remote_url:
                download = httpx.get(str(remote_url), timeout=300)
                download.raise_for_status()
                raw = download.content
            else:
                raise ProviderError("OpenAI image response contains neither b64_json nor URL")
            with Image.open(io.BytesIO(raw)) as source:
                rendered = source.convert("RGB")
                rendered.thumbnail((1344, 768), Image.Resampling.LANCZOS)
                canvas = Image.new("RGB", (1344, 768), (0, 0, 0))
                x = (canvas.width - rendered.width) // 2
                y = (canvas.height - rendered.height) // 2
                canvas.paste(rendered, (x, y))
                destination.parent.mkdir(parents=True, exist_ok=True)
                canvas.save(destination, format="PNG")
        except ProviderError:
            raise
        except Exception as error:
            raise ProviderError(
                f"OpenAI image generation failed: {type(error).__name__}: {error}"
            ) from error
        return GeneratedFile(
            path=destination,
            receipt=ProviderReceipt(
                provider=self.name,
                model=self.model,
                operation="image_generation",
                request_sha256=sha256_json(request_identity),
                provider_request_id=str(getattr(response, "id", "")) or None,
            ),
            metadata={"width": 1344, "height": 768, "reference_count": len(references)},
        )
