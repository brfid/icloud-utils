"""Check local Markdown destinations in source and deployed documentation."""
import re
from urllib.parse import unquote, urlsplit


def local_link_errors(paths):
    errors = []
    for path in paths:
        for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', path.read_text()):
            link = urlsplit(target)
            if link.scheme or link.netloc:
                continue
            destination = path.parent / unquote(link.path) if link.path else path
            if not destination.is_file():
                errors.append(f'{path}: missing {target}')
            elif link.fragment and destination.suffix == '.md':
                headings = re.findall(r'^#{1,6}\s+(.+)$', destination.read_text(), re.MULTILINE)
                anchors = {re.sub(r'[^\w -]', '', heading.lower()).replace(' ', '-') for heading in headings}
                if unquote(link.fragment) not in anchors:
                    errors.append(f'{path}: missing anchor {target}')
    return errors
