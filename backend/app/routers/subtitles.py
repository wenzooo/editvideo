from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..db import get_db
from ..models import SubtitleSegment, Video, VideoStatus
from ..schemas import SubtitleSegmentOut, SubtitlesReplace

router = APIRouter(prefix="/api/videos/{video_id}/subtitles", tags=["subtitles"],
                   dependencies=[Depends(require_auth)])


def _get_video(db: Session, video_id: str) -> Video:
    video = db.get(Video, video_id)
    if not video:
        raise HTTPException(404, "Video non trovato")
    return video


@router.get("", response_model=list[SubtitleSegmentOut])
def get_subtitles(video_id: str, db: Session = Depends(get_db)):
    video = _get_video(db, video_id)
    return [SubtitleSegmentOut.model_validate(s) for s in video.segments]


@router.put("", response_model=list[SubtitleSegmentOut])
def replace_subtitles(video_id: str, body: SubtitlesReplace, db: Session = Depends(get_db)):
    video = _get_video(db, video_id)
    if video.status in VideoStatus.BUSY:
        raise HTTPException(409, "Video in lavorazione: attendi la fine del job")

    # I word-timestamp (karaoke) sopravvivono al salvataggio: se una caption
    # rientra con gli stessi tempi e lo stesso testo, eredita le sue parole.
    # Solo le caption realmente modificate nel testo perdono il karaoke.
    existing: dict[tuple[float, float], tuple[str, list | None]] = {
        (round(s.start, 2), round(s.end, 2)): (s.text, s.words)
        for s in video.segments
    }

    segs = sorted(
        (s for s in body.segments if s.text.strip() and s.end > s.start),
        key=lambda s: s.start,
    )
    db.execute(delete(SubtitleSegment).where(SubtitleSegment.video_id == video_id))
    for i, s in enumerate(segs):
        text = s.text.strip()
        prev = existing.get((round(s.start, 2), round(s.end, 2)))
        words = prev[1] if prev and prev[0] == text else None
        db.add(SubtitleSegment(video_id=video_id, idx=i,
                               start=round(s.start, 3), end=round(s.end, 3),
                               text=text, words=words))
    db.commit()
    db.refresh(video)
    return [SubtitleSegmentOut.model_validate(s) for s in video.segments]
