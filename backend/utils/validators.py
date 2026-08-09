import urllib.parse
import re

def extract_youtube_video_id(url: str) -> str:
    """
    Extract the 11-character video ID from a YouTube URL.
    Raises ValueError if invalid.
    """
    parsed = urllib.parse.urlparse(url)
    
    if parsed.hostname in ('youtu.be', 'www.youtu.be'):
        video_id = parsed.path.lstrip('/')
    elif parsed.hostname in ('youtube.com', 'www.youtube.com'):
        if parsed.path.startswith('/embed/'):
            video_id = parsed.path.split('/embed/')[1]
        elif parsed.path.startswith('/v/'):
            video_id = parsed.path.split('/v/')[1]
        elif parsed.path.startswith('/shorts/'):
            video_id = parsed.path.split('/shorts/')[1]
        elif parsed.path == '/watch':
            qs = urllib.parse.parse_qs(parsed.query)
            video_id = qs.get('v', [None])[0]
        else:
            raise ValueError(f"Could not parse YouTube URL path: {url}")
    else:
        raise ValueError(f"Not a YouTube domain: {url}")
        
    if not video_id:
        raise ValueError(f"Could not extract video ID from URL: {url}")
        
    # Standardize to get just the ID without extra paths/params
    video_id = video_id.split('?')[0].split('&')[0].split('/')[0]
    
    if not re.match(r'^[a-zA-Z0-9_-]{11}$', video_id):
        raise ValueError(f"Extracted video ID '{video_id}' is not 11 characters long")
        
    return video_id
