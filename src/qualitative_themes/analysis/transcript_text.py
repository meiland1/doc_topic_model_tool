"""Remove recognizable transcript scaffolding from derived model input only."""
import re

MODEL_TEXT_VERSION = 'transcript-markers-v1'
_TIME = r'\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?'
_RANGE = _TIME + r'(?:[ \t]*(?:-->|[-–])[ \t]*' + _TIME + r')?'
# Bracketed timestamps are metadata anywhere; bare timestamps must lead a line.
_BRACKET_TIME = re.compile(r'[\[(][ \t]*' + _RANGE + r'[ \t]*[\])]')
_PREFIX_TIME = re.compile(r'(?m)^[ \t]*' + _RANGE + r'(?=[ \t]|$)[ \t]*')
_SPEAKER = re.compile(r'(?i)(?<!\w)(?:\[|\()?speaker[_ -]\d+(?:\]|\))?[ \t]*:?[ \t]*(?!\w)')
# Named roles are stripped only when formatted as a turn label, never in prose.
_ROLE = re.compile(r'(?im)^[ \t]*(?:interviewer|interviewee|participant(?:[ _-]\d+)?)[ \t]*:[ \t]*')


def clean_model_text(text):
    text = _BRACKET_TIME.sub(' ', text)
    text = _PREFIX_TIME.sub('', text)
    text = _SPEAKER.sub(' ', text)
    text = _ROLE.sub('', text)
    return ' '.join(text.split())
