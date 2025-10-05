import logging
import re
import base64
from struct import pack
from pyrogram.file_id import FileId
from pymongo.errors import DuplicateKeyError
from umongo import Instance, Document, fields
from motor.motor_asyncio import AsyncIOMotorClient
from marshmallow.exceptions import ValidationError
from info import DATABASE_URI, DATABASE_NAME, COLLECTION_NAME, USE_CAPTION_FILTER

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# MongoDB connection
client = AsyncIOMotorClient(DATABASE_URI)
db = client[DATABASE_NAME]
instance = Instance.from_db(db)


@instance.register
class Media(Document):
    """
    Represents a stored media document in MongoDB.
    Compatible with modern uMongo versions.
    """

    _id = fields.StringField(required=True, attribute='_id')  # Unique ID for file
    file_ref = fields.StringField(allow_none=True)
    file_name = fields.StringField(required=True)
    file_size = fields.IntegerField(required=True)
    file_type = fields.StringField(allow_none=True)
    mime_type = fields.StringField(allow_none=True)
    caption = fields.StringField(allow_none=True)

    class Meta:
        indexes = ('$file_name',)
        collection_name = COLLECTION_NAME


# ====================================================
# Database Operations
# ====================================================

async def save_file(media):
    """Save a media file in the database."""
    file_id, file_ref = unpack_new_file_id(media.file_id)
    file_name = re.sub(r"(_|\-|\.|\+)", " ", str(media.file_name))

    try:
        file_doc = Media(
            _id=file_id,
            file_ref=file_ref,
            file_name=file_name,
            file_size=media.file_size,
            file_type=media.file_type,
            mime_type=media.mime_type,
            caption=media.caption.html if media.caption else None,
        )
    except ValidationError:
        logger.exception('Error validating Media document before saving.')
        return False, 2

    try:
        await file_doc.commit()
        logger.info(f'{media.file_name} saved successfully.')
        return True, 1
    except DuplicateKeyError:
        logger.warning(f'{media.file_name} already exists in database.')
        return False, 0
    except Exception as e:
        logger.exception(f"Unexpected error saving file: {e}")
        return False, 2


async def get_search_results(query, file_type=None, max_results=10, offset=0):
    """Return search results based on filename or caption."""
    query = query.strip()

    if not query:
        raw_pattern = '.'
    elif ' ' not in query:
        raw_pattern = r'(\b|[\.\+\-_])' + re.escape(query) + r'(\b|[\.\+\-_])'
    else:
        raw_pattern = re.escape(query).replace(r'\ ', r'.*[\s\.\+\-_]')

    try:
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except re.error:
        return [], '', 0

    if USE_CAPTION_FILTER:
        filter_query = {'$or': [{'file_name': regex}, {'caption': regex}]}
    else:
        filter_query = {'file_name': regex}

    if file_type:
        filter_query['file_type'] = file_type

    total_results = await Media.count_documents(filter_query)
    next_offset = offset + max_results if offset + max_results < total_results else ''

    cursor = Media.find(filter_query)
    cursor.sort('$natural', -1).skip(offset).limit(max_results)
    files = await cursor.to_list(length=max_results)

    return files, next_offset, total_results


async def get_file_details(file_id_query):
    """Retrieve full document details for a specific file ID."""
    cursor = Media.find({'_id': file_id_query})
    file_details = await cursor.to_list(length=1)
    return file_details


# ====================================================
# Utility Encoding/Decoding Helpers
# ====================================================

def encode_file_id(s: bytes) -> str:
    r = b""
    n = 0
    for i in s + bytes([22]) + bytes([4]):
        if i == 0:
            n += 1
        else:
            if n:
                r += b"\x00" + bytes([n])
                n = 0
            r += bytes([i])
    return base64.urlsafe_b64encode(r).decode().rstrip("=")


def encode_file_ref(file_ref: bytes) -> str:
    return base64.urlsafe_b64encode(file_ref).decode().rstrip("=")


def unpack_new_file_id(new_file_id):
    """Extract file_id and file_ref from a Pyrogram file ID."""
    decoded = FileId.decode(new_file_id)
    file_id = encode_file_id(
        pack(
            "<iiqq",
            int(decoded.file_type),
            decoded.dc_id,
            decoded.media_id,
            decoded.access_hash
        )
    )
    file_ref = encode_file_ref(decoded.file_reference)
    return file_id, file_ref
