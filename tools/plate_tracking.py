"""Track observed plate regions; a plate string never determines object identity."""
from collections import deque
from dataclasses import dataclass, field
import math


def iou(first, second):
    left = max(first[0], second[0]); top = max(first[1], second[1])
    right = min(first[0] + first[2], second[0] + second[2])
    bottom = min(first[1] + first[3], second[1] + second[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    total = first[2] * first[3] + second[2] * second[3] - intersection
    return intersection / total if total > 0 else 0


@dataclass
class Track:
    identifier: int
    box: list
    last_seen: float
    evidence: deque = field(default_factory=lambda: deque(maxlen=3))
    confirmed_plate: str | None = None
    announced: bool = False
    last_verified: str | None = None
    appearance: list | None = None


class PlateTracker:
    def __init__(self):
        self.tracks = {}
        self.next_identifier = 1
        self.last_frame = None

    def update(self, observations, frame_time, frame_id, scene_cut=False):
        if scene_cut:
            self.tracks.clear()
        self.tracks = {identifier: track for identifier, track in self.tracks.items()
                       if frame_time - track.last_seen <= 3}
        pairs = []
        for index, observation in enumerate(observations):
            box = observation["box"]
            for identifier, track in self.tracks.items():
                appearance = observation.get("appearance")
                if appearance and track.appearance:
                    if sum(abs(first - second) for first, second in zip(appearance, track.appearance)) > .8:
                        continue
                overlap = iou(box, track.box)
                center_distance = math.hypot(box[0] + box[2] / 2 - track.box[0] - track.box[2] / 2,
                                             box[1] + box[3] / 2 - track.box[1] - track.box[3] / 2)
                size = max(1, math.hypot(box[2], box[3]))
                if overlap > .12 or center_distance < size * .55:
                    pairs.append((overlap + .15 * max(0, 1 - center_distance / size), index, identifier))
        assignments = {}; used_tracks = set()
        for _, index, identifier in sorted(pairs, reverse=True):
            if index not in assignments and identifier not in used_tracks:
                assignments[index] = identifier; used_tracks.add(identifier)
        results = []
        for index, observation in enumerate(observations):
            identifier = assignments.get(index)
            if identifier is None:
                identifier = self.next_identifier; self.next_identifier += 1
                self.tracks[identifier] = Track(identifier, observation["box"], frame_time,
                                               appearance=observation.get("appearance"))
            track = self.tracks[identifier]
            track.box = observation["box"]; track.last_seen = frame_time
            if observation.get("appearance"):
                track.appearance = ([old * .7 + new * .3 for old, new in zip(track.appearance, observation["appearance"])]
                                    if track.appearance else observation["appearance"])
            plate = observation.get("plate")
            if frame_id != self.last_frame:
                track.evidence.append(plate)
            stable = bool(plate and len(track.evidence) == 3 and all(value == plate for value in track.evidence))
            if not stable:
                track.confirmed_plate = None
            elif track.confirmed_plate != plate:
                track.confirmed_plate = plate
            newly_confirmed = stable and not track.announced
            previous_plate = track.last_verified
            changed = bool(stable and previous_plate and previous_plate != plate)
            if newly_confirmed:
                track.announced = True
            if stable:
                track.last_verified = plate
            results.append({**observation, "trackId": identifier, "confirmed": stable,
                            "newlyConfirmed": newly_confirmed,
                            "state": "confirmed" if stable else "uncertain" if plate else "unreadable",
                            "evidenceCount": sum(value == plate for value in track.evidence) if plate else 0,
                            "changedConfirmed": changed, "previousPlate": previous_plate,
                            "candidates": sorted(set(filter(None, track.evidence)))})
        self.last_frame = frame_id
        # An empty frame closes the pass. A later vehicle, even with the same plate, gets a new ID.
        if not observations:
            self.tracks.clear()
        return results
