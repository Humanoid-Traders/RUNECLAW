# RUNECLAW 3D agent model

Drop the AI Viking agent's 3D model here as:

    app/public/mascot/agent.glb

The moment that file exists, the real-time WebGL viewer (`js/mascot3d.js`,
three.js vendored under `vendor/three/`) picks it up automatically:

- The dedicated showcase page **`/agent`** renders it full-size (drag to orbit).
- The **landing hero** shows it top-right (hidden until the model is present, so
  nothing on the landing changes until then).

## Model requirements

- **Format:** glTF-binary (`.glb`), single self-contained file.
- **Up axis:** Y-up (the viewer auto-centres, scales to a fixed height, and sits
  the model's feet on the rune-disc, so exact scale/offset don't matter).
- **Animation (optional):** if the GLB contains an animation clip, the first clip
  plays on loop (idle / breathing). With no clip, the viewer adds a soft idle bob.
- **Textures:** embedded PBR (metallic-roughness). Room-environment lighting is
  applied, plus a white key light and a rune-blue rim/fill.
- **Compression:** prefer **uncompressed** geometry. (Draco/KTX2 would need their
  decoders vendored — ask and we'll add them.)
- **Budget:** keep it web-friendly — ideally < 8 MB and < ~150k triangles so it
  loads fast and runs at 60 fps on phones.

## How to produce agent.glb from the character art

If you don't already have a 3D model, generate one from the RUNECLAW character
render with an image-to-3D tool (e.g. Meshy, Tripo, Rodin, Luma Genie), then
export **GLB**. A rigged idle animation is a nice-to-have, not required.

Commit the file to the repo (or hand it over) and the agent goes live.
