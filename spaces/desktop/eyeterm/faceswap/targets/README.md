# Face-Swap Target Presets

Put face images here and reference them by filename (without extension) via
`--target-preset NAME`.

## Rules

- **Never commit face images to the repo.** `.gitignore` blocks all image files.
- **Use images you have the right to use:**
  - Synthetic faces from `thispersondoesnotexist.com` (StyleGAN output, no real person)
  - Your own face (for testing)
  - Public-domain images with clear attribution
  - Stock photos you've licensed
- **Do not use** real identifiable people (especially public figures) without consent.
  Personating someone in a live meeting may be illegal (DE: §201a StGB,
  US: identity-misuse statutes, plus insightface's non-commercial license terms).

## Layout

```
targets/
  alice.jpg       # example preset, called via --target-preset alice
  test.jpg
  my-face.jpg
```

Accepted extensions: jpg, jpeg, png, webp.

## Image requirements

- Single face, clearly visible, frontal preferred (insightface handles slight angles)
- At least 256×256 px; 512×512 or higher gives better embedding quality
- Good lighting, no heavy shadows on the face
- One face per image (if multiple, the largest is picked)

## Fallback to full path

If you don't want presets, use `--deepfake-target /absolute/path/to/face.jpg`.
That bypasses the preset resolver entirely.
