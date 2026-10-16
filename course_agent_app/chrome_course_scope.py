"""Explicit current course profiles, independent of the legacy sandbox defaults."""
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True)
class Course:
    id: str
    label: str
    origin: str = "https://moodle.hku.hk"

    @property
    def url(self):
        return f"{self.origin}/course/view.php?id={self.id}"

    def url_for(self, path, query):
        from urllib.parse import urlencode
        return f"{self.origin}{path}?{urlencode(query)}"

    def check_origin(self, url):
        p = urlparse(url)
        if (f"{p.scheme}://{p.netloc}" != self.origin or p.username or p.password):
            raise ValueError("Page left the selected Moodle host; no action is permitted.")


COURSES = {c.id: c for c in (Course("140147", "Class A"), Course("140148", "Class B"))}


def course(value):
    if str(value) not in COURSES:
        raise ValueError("Choose current Class A (140147) or Class B (140148).")
    return COURSES[str(value)]


class MoodleLoginRequired(ValueError):
    pass


def course_identity(page, selected):
    selected.check_origin(page.url)
    if urlparse(page.url).path.startswith('/login/') or page.locator('#loginbtn, form#login').count():
        raise MoodleLoginRequired('Moodle requires login in the connected Chrome profile.')
    ids = page.evaluate("""() => {
      const out = [];
      for (const x of document.body.classList) {
        const m = /^course-(\\d+)$/.exec(x); if (m) out.push(m[1]);
      }
      return out;
    }""")
    if str(selected.id) not in ids or any(i != selected.id for i in ids):
        raise ValueError("The live page does not identify the exact selected course.")
    # Group settings contain an enrolment-key password widget even when logged in.
    # A generic password-input check would reject every legitimate group form.
