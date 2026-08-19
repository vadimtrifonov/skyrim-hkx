# Havok Fields

## Animation

- `hkaSplineCompressedAnimation`: duration, frame and track counts, compressed transforms, annotations.
- `hkaAnimationBinding`: skeleton name, transform-track mapping, blend hint.
- `hkaDefaultAnimatedReferenceFrame`: extracted-motion samples.
- Root-bone motion may also be encoded in a compressed transform track.

An empty `transformTrackToBoneIndices` contains no explicit mapping; resolve track order against the applicable skeleton.

## Behavior

- `hkbClipGenerator`: animation name and playback parameters.
- `hkbClipTriggerArray`: behavior-owned timed events.
- `hkbBehaviorGraphStringData`: event names indexed by event ID.
- Behavior objects form a shared reference graph; an object can have multiple incoming references.
