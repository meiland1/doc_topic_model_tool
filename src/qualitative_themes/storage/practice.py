"""Small bundled synthetic corpus; every start creates a separate editable project."""
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from qualitative_themes.storage.database import create_project, connect
from qualitative_themes.storage.documents import import_document


EXAMPLES = (
    ('Practice interview A.txt', 'A', (
        'The bus runs only twice in the morning. I missed an appointment when the first bus was late.',
        'My neighbour offered a lift. Asking for help made the journey possible, but I worried about relying on her.',
        'The community garden is close enough to walk to. Sharing seeds helped me get to know people.',
        'We disagree about watering schedules. A shared garden needs time and negotiation as well as friendship.',
        'I use the library computer to book appointments. The website is easier when someone explains each step.',
        'A paper reminder works better for me than an app. Having a computer does not always make a task easier.')),
    ('Practice interview B.txt', 'B', (
        'I cycle to the clinic when the weather is good. During winter I pay for a taxi and postpone less urgent visits.',
        'Evening appointments would help people who work. The bus timetable is only part of the difficulty.',
        'I enjoy growing vegetables with other residents. We exchange recipes and check on each other.',
        'I stopped attending the garden meetings because they overlapped with work. Being welcome did not mean I could take part.',
        'I can book online confidently. My father prefers calling because he wants to ask questions before choosing a time.',
        'The library computers are useful but there is a queue. Access also means having enough time to finish.')),
    ('Practice interview C.txt', 'C', (
        'The clinic is nearby, so transport is not a problem for me. Finding childcare for an appointment is harder.',
        'A friend watches my children sometimes. I would like a service I could arrange without feeling I owe a favour.',
        'The garden gives us a place to meet. My children enjoy it even when we do not grow much food.',
        'Some residents prefer a quiet garden. We are still discussing how to share the space fairly.',
        'I read appointment letters on my phone. Small text and unfamiliar terms make them difficult to understand.',
        'A staff member explained the letter clearly. I needed an explanation rather than another reminder.')),
)


def create_practice_project():
    """Make a new practice project with three short synthetic interviews.

    Post: a fresh project under app_data/practice-projects/<random>, never
    reused, so trying things out can't damage real data.
    """
    from qualitative_themes.runtime.paths import app_data_dir
    root = app_data_dir() / 'practice-projects'
    project = create_project(root / uuid4().hex, 'Practice — fictional community interviews')
    with TemporaryDirectory() as temp:
        for name, speaker, answers in EXAMPLES:
            source = Path(temp) / name
            text = 'SYNTHETIC PRACTICE MATERIAL — invented examples, not research data.\n\n'
            text += '\n\n'.join(f'Interviewer: Practice question {i+1}: tell me more about your experience.\n'
                                f'Participant {speaker}: {answer}' for i, answer in enumerate(answers))
            source.write_text(text, encoding='utf-8')
            import_document(project.directory, source)
    with connect(project.directory) as conn:
        conn.execute("INSERT INTO history_events VALUES (?, ?, 'practice_project_created', ?)",
                     (str(uuid4()), datetime.now(timezone.utc).isoformat(), json.dumps({'fixture_version': 1})))
    return project


def is_practice_project(directory):
    """True for projects made by create_practice_project (the UI shows a banner)."""
    with connect(directory) as conn:
        return conn.execute("SELECT 1 FROM history_events WHERE event_type='practice_project_created' LIMIT 1").fetchone() is not None
