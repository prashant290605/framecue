"""Generate the public "Try sample" screenplay: The Harbor Light.

The story, characters and every line here are original and fictional. It was written for
FrameCue and is not derived from any real or confidential screenplay, so it is safe to
publish. It uses the same timestamped format as the parser expects.

Usage (from the repository root):
    .venv/Scripts/python frontend/scripts/make_sample.py
"""

from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "public" / "sample"

# (kind, label, text). kind: C = camera shot (starts a new shot), A = action, D = dialogue,
# S = sound effect. Camera shots run until the next shot or the end of the scene.
EPISODES = [
    {
        "number": 1,
        "logline": "A lighthouse keeper hears a dead man's voice on the storm radio.",
        "scenes": [
            (
                "INT. LIGHTHOUSE LAMP ROOM – NIGHT",
                [
                    ("C", "CAMERA", "Wide shot of the lamp room as the great lens turns slowly."),
                    ("A", "ACTION", "Mara wipes salt spray from the glass with a rag."),
                    ("S", "SOUND FX", "Thunder rolls across the bay."),
                    (
                        "C",
                        "CAMERA",
                        "Close-up on Mara, exhausted, rain streaking the window behind her.",
                    ),
                    ("D", "MARA (Tired)", "Third storm this week. The lens won't last the winter."),
                    ("C", "CAMERA", "Insert on the desk radio as its dial light flickers."),
                    ("S", "SOUND FX", "Static, then a faint voice breaking through."),
                    (
                        "D",
                        "VOICE ON RADIO (O.S., Distorted)",
                        "Harbor Light, do you read? A boat is coming in without lights.",
                    ),
                    ("A", "ACTION", "Mara snatches up the radio handset."),
                    ("D", "MARA (Urgent)", "Who is this? Identify your vessel."),
                    ("D", "VOICE ON RADIO (O.S.)", "Tell Mara her brother is on board."),
                    ("C", "CAMERA", "Extreme close-up on Mara's face, frozen."),
                    ("D", "MARA (Whispering)", "Elias has been dead for six years."),
                ],
            ),
            (
                "INT. LIGHTHOUSE STAIRS – NIGHT",
                [
                    ("C", "CAMERA", "Tracking shot as Mara runs down the spiral stairs."),
                    ("A", "ACTION", "Her lantern swings wildly against the stone walls."),
                    ("S", "SOUND FX", "A heavy knock on the iron door below."),
                    ("C", "CAMERA", "Low angle on the door as rainwater pours beneath it."),
                    ("D", "MARA (Breathless)", "Who's there?"),
                    ("D", "ELIAS (O.S., Hoarse)", "You kept the light burning. I knew you would."),
                    ("A", "ACTION", "Mara slides the bolt back with shaking hands."),
                    (
                        "C",
                        "CAMERA",
                        "The door swings open on a soaked man holding a sealed envelope.",
                    ),
                    (
                        "D",
                        "ELIAS (Quiet)",
                        "This was never meant for you. But you have to read it before dawn.",
                    ),
                    (
                        "C",
                        "CAMERA",
                        "Close-up on the wax seal, pressed with the harbor master's crest.",
                    ),
                    ("D", "MARA (Stunned)", "That's Captain Rhee's seal."),
                ],
            ),
            (
                "EXT. LIGHTHOUSE GALLERY – NIGHT",
                [
                    ("C", "CAMERA", "Mara and Elias on the gallery as the storm begins to ease."),
                    ("D", "MARA (Shaken)", "Where have you been all this time?"),
                    ("D", "ELIAS (Evasive)", "On the boats Rhee said were lost."),
                    ("A", "ACTION", "Elias rolls up his sleeve to show a rope-burn scar."),
                    ("C", "CAMERA", "Close-up on the scar, pale against his wet skin."),
                    ("D", "ELIAS (Low)", "He keeps the crews quiet. One way or another."),
                    ("S", "SOUND FX", "A distant engine coughs out on the water."),
                    ("C", "CAMERA", "Mara turns toward the dark bay."),
                    ("D", "MARA (Alarmed)", "Someone's out there right now. With no lights."),
                ],
            ),
        ],
    },
    {
        "number": 2,
        "logline": "A sealed letter suggests the harbor's wrecks were no accident.",
        "scenes": [
            (
                "INT. LIGHTHOUSE KITCHEN – NIGHT",
                [
                    (
                        "C",
                        "CAMERA",
                        "Elias drips by the stove while Mara keeps the table between them.",
                    ),
                    ("D", "MARA (Cold)", "Six years. Not one word."),
                    ("D", "ELIAS (Defensive)", "Rhee told everyone I drowned. It kept me alive."),
                    ("A", "ACTION", "Mara breaks the wax seal with her thumb."),
                    ("S", "SOUND FX", "Paper tears."),
                    ("C", "CAMERA", "Insert on the letter: a column of ship names and dates."),
                    ("D", "MARA (Reading, Confused)", "These are the boats lost on the reef."),
                    ("D", "ELIAS (Grim)", "They weren't lost. They were steered there."),
                    ("C", "CAMERA", "Mara looks up at the lens turning in the dark above them."),
                    ("D", "MARA (Realising)", "Someone dimmed the light on those nights."),
                ],
            ),
            (
                "EXT. HARBOR PIER – DAWN",
                [
                    ("C", "CAMERA", "Wide shot of the empty pier in grey morning light."),
                    ("A", "ACTION", "Captain Rhee walks the boards with his collar turned up."),
                    ("D", "RHEE (Friendly)", "Mara. You look like you haven't slept."),
                    ("D", "MARA (Guarded)", "The radio kept me up. A boat came in without lights."),
                    ("C", "CAMERA", "Close-up on Rhee's hand tightening around his cane."),
                    ("D", "RHEE (Too Calm)", "No boat came in last night. I'd have known."),
                    ("A", "ACTION", "Mara steps back toward the lighthouse path."),
                    (
                        "D",
                        "RHEE (Soft, Threatening)",
                        "Whatever you found, burn it. For Elias's sake.",
                    ),
                    ("C", "CAMERA", "Extreme close-up on Mara as the gulls go quiet."),
                    ("D", "MARA (Whispering)", "How do you know about Elias?"),
                ],
            ),
            (
                "INT. HARBOR OFFICE – DAY",
                [
                    ("C", "CAMERA", "Rhee's office, sea charts pinned to every wall."),
                    ("A", "ACTION", "Mara slips through the unlocked door and closes it softly."),
                    (
                        "D",
                        "MARA (Whispering, To Herself)",
                        "A chart for every night a boat went down.",
                    ),
                    ("C", "CAMERA", "Insert on a chart: the reef circled in red ink."),
                    ("S", "SOUND FX", "Footsteps climb the wooden stairs outside."),
                    ("A", "ACTION", "Mara folds the chart into her coat."),
                    ("C", "CAMERA", "The brass door handle begins to turn."),
                    ("D", "RHEE (O.S., Sharp)", "Who's in my office?"),
                    ("C", "CAMERA", "Mara presses herself flat behind the door."),
                ],
            ),
        ],
    },
    {
        "number": 3,
        "logline": "Mara sets a trap in the dark lamp room as a ship drifts toward the reef.",
        "scenes": [
            (
                "INT. LIGHTHOUSE LAMP ROOM – DUSK",
                [
                    ("C", "CAMERA", "Mara and Elias spread the letter beside the lamp controls."),
                    ("D", "ELIAS (Urgent)", "He'll come tonight. He still has a key to this room."),
                    ("A", "ACTION", "Mara wires a small brass bell to the trapdoor hinge."),
                    ("S", "SOUND FX", "Wind howls through the iron gallery."),
                    ("D", "MARA (Determined)", "Then we let him think the light is his."),
                    ("C", "CAMERA", "The lens slows as Mara pulls the main power lever."),
                    ("S", "SOUND FX", "The motor winds down into silence."),
                    ("C", "CAMERA", "Darkness. Only Elias's lantern glows."),
                    ("D", "ELIAS (Uneasy)", "If a boat is out there, we just blinded it."),
                    ("D", "MARA (Firm)", "Two minutes. That's all we need."),
                ],
            ),
            (
                "INT. LIGHTHOUSE STAIRS – NIGHT",
                [
                    ("S", "SOUND FX", "The trapdoor bell rings once."),
                    ("C", "CAMERA", "Low angle as a figure climbs the stairs behind a lantern."),
                    ("D", "RHEE (O.S., Calling)", "Mara? The light's gone out. Let me help."),
                    ("A", "ACTION", "Mara steps into the lantern light, holding the letter."),
                    (
                        "D",
                        "MARA (Steady)",
                        "Help the way you helped the Seabird? And the Northern Star?",
                    ),
                    ("C", "CAMERA", "Rhee's face hardens."),
                    ("D", "RHEE (Cold)", "Give me the letter."),
                    ("A", "ACTION", "Elias steps out of the shadows behind him."),
                    ("D", "ELIAS (Quiet)", "Hello, Captain."),
                    ("C", "CAMERA", "Extreme close-up on Rhee as the lens flickers back to life."),
                    ("S", "SOUND FX", "A ship's horn blares close to the rocks."),
                    ("D", "MARA (Alarmed)", "That's a boat. Right on the reef."),
                ],
            ),
            (
                "EXT. THE REEF – NIGHT",
                [
                    ("C", "CAMERA", "A fishing boat drifts toward the rocks, its deck lamps dark."),
                    ("S", "SOUND FX", "Waves crash against the hull."),
                    ("C", "CAMERA", "The lighthouse beam sweeps back across the water."),
                    ("A", "ACTION", "The crew scramble to haul the wheel around."),
                    ("D", "CREW MEMBER (Shouting)", "Hard to port! The light's back!"),
                    (
                        "C",
                        "CAMERA",
                        "Wide shot as the boat slides past the reef with a metre to spare.",
                    ),
                    ("D", "ELIAS (O.S., Exhausted)", "We made it."),
                    ("C", "CAMERA", "Mara watches from the gallery, the letter still in her hand."),
                    ("D", "MARA (Quiet)", "Not all of them. Not yet."),
                ],
            ),
        ],
    },
]


def ts(ms: int) -> str:
    return f"{ms // 60000:02d}:{ms // 1000 % 60:02d}.{ms % 1000 // 10:02d}"


def duration(kind: str, text: str) -> int:
    if kind == "D":
        return 700 + 330 * len(text.split())
    return {"A": 2600, "S": 1500}[kind]


def render(ep: dict) -> str:
    lines = [
        f"EPISODE NUMBER: Episode {ep['number']}",
        f"EPISODE LOGLINE: {ep['logline']}",
        "CONTINUITY NOTES: Fictional sample written for FrameCue. Not derived from any real "
        "screenplay.",
        "",
        "--- SCRIPT START ---",
        "",
    ]
    cursor = 0
    for n, (heading, beats) in enumerate(ep["scenes"], start=1):
        start = cursor
        events: list[tuple[str, int, int, str]] = []
        shot: list = []  # open camera shot: [text, start]
        for kind, label, text in beats:
            if kind == "C":
                if shot:
                    events.append(("CAMERA", shot[1], cursor, shot[0]))
                shot = [text, cursor]
                cursor += 400
                continue
            length = duration(kind, text)
            body = f'"{text}"' if kind == "D" else text
            events.append((label, cursor, cursor + length, body))
            cursor += length + 250
        cursor += 600
        if shot:
            events.append(("CAMERA", shot[1], cursor, shot[0]))
        events.sort(key=lambda e: (e[1], e[0] != "CAMERA"))
        lines += [
            f"SCENE {n:02d}",
            f"Scene heading: {heading}",
            f"Scene start: {ts(start)}",
            f"Scene end: {ts(cursor)}",
            "",
            "Actions and dialogue:",
            "",
        ]
        for label, a, b, body in events:
            lines += [f"**{label}:**", f"Start: ({ts(a)}) | End: ({ts(b)})", body, ""]
    lines.append("--- SCRIPT END ---")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for ep in EPISODES:
        path = OUT / f"harbor-light-{ep['number']:02d}.txt"
        path.write_text(render(ep), encoding="utf-8", newline="\n")
        print("wrote", path.relative_to(OUT.parents[1]))


if __name__ == "__main__":
    main()
