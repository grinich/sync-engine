from sqlalchemy import Column, Integer, String, Boolean, ForeignKey
from sqlalchemy import event
from sqlalchemy.orm import relationship, backref
from sqlalchemy.sql.expression import false

from sqlalchemy.orm.collections import attribute_mapped_collection
from sqlalchemy.schema import UniqueConstraint


from inbox.sqlalchemy_ext.util import (generate_public_id,
                                       propagate_soft_delete)

from inbox.models.transaction import HasRevisions
from inbox.models.base import MailSyncBase
from inbox.models.constants import MAX_INDEXABLE_LENGTH
from inbox.models.namespace import Namespace


class Tag(MailSyncBase, HasRevisions):
    """Tags represent extra data associated with threads.

    A note about the schema. The 'public_id' of a tag is immutable. For
    reserved tags such as the inbox or starred tag, the public_id is a fixed
    human-readable string. For other tags, the public_id is an autogenerated
    uid similar to a normal public id, but stored as a string for
    compatibility.

    The name of a tag is allowed to be mutable, to allow for the eventuality
    that users wish to change the name of user-created labels, or that we
    someday expose localized names ('DAS INBOX'), or that we somehow manage to
    sync renamed gmail labels, etc.
    """

    namespace = relationship(
        Namespace, backref=backref(
            'tags',
            primaryjoin='and_(Tag.namespace_id == Namespace.id, '
                        'Tag.deleted_at.is_(None))',
            collection_class=attribute_mapped_collection('public_id')),
        primaryjoin='and_(Tag.namespace_id==Namespace.id, '
        'Namespace.deleted_at.is_(None))',
        load_on_pending=True)
    # (Because this class inherits from HasRevisions, we need
    # load_on_pending=True here so that setting Transaction.namespace in
    # Transaction.set_extra_attrs() doesn't raise an IntegrityError.)
    namespace_id = Column(Integer, ForeignKey(
        'namespace.id', ondelete='CASCADE'), nullable=False)

    public_id = Column(String(MAX_INDEXABLE_LENGTH), nullable=False,
                       default=generate_public_id)
    name = Column(String(MAX_INDEXABLE_LENGTH), nullable=False)

    user_created = Column(Boolean, server_default=false(), nullable=False)

    RESERVED_PROVIDER_NAMES = ['gmail', 'outlook', 'yahoo', 'exchange',
                               'inbox', 'icloud', 'aol']

    CANONICAL_TAG_NAMES = ['inbox', 'archive', 'drafts', 'sending', 'sent',
                           'spam', 'starred', 'trash', 'unread', 'unseen',
                           'attachment']

    RESERVED_TAG_NAMES = ['all', 'archive', 'drafts', 'send', 'replied',
                          'file', 'attachment', 'unseen']

    # Tags that are allowed to be both added and removed via the API.
    USER_MUTABLE_TAGS = ['unread', 'starred', 'spam', 'trash', 'inbox',
                         'archive']

    @property
    def user_removable(self):
        # The 'unseen' tag can only be removed.
        return (self.user_created or self.public_id in self.USER_MUTABLE_TAGS
                or self.public_id == 'unseen')

    @classmethod
    def create_canonical_tags(cls, namespace, db_session):
        """If they don't already exist yet, create tags that should always
        exist."""
        existing_canonical_tags = db_session.query(Tag).filter(
            Tag.namespace_id == namespace.id,
            Tag.public_id.in_(cls.CANONICAL_TAG_NAMES)).all()
        missing_canonical_names = set(cls.CANONICAL_TAG_NAMES).difference(
            {tag.public_id for tag in existing_canonical_tags})
        for canonical_name in missing_canonical_names:
            tag = Tag(namespace=namespace,
                      public_id=canonical_name,
                      name=canonical_name)
            db_session.add(tag)

    @classmethod
    def name_available(cls, name, namespace_id, db_session):
        if any(name.lower().startswith(provider) for provider in
               cls.RESERVED_PROVIDER_NAMES):
            return False

        if name in cls.RESERVED_TAG_NAMES or name in cls.CANONICAL_TAG_NAMES:
            return False

        if (name,) in db_session.query(Tag.name). \
                filter(Tag.namespace_id == namespace_id).all():
            return False

        return True

    @property
    def user_addable(self):
        return (self.user_created or self.public_id in self.USER_MUTABLE_TAGS)

    __table_args__ = (UniqueConstraint('namespace_id', 'name'),
                      UniqueConstraint('namespace_id', 'public_id'))


@event.listens_for(Tag, 'after_update')
def _after_tag_update(mapper, connection, target):
    """ Hook to cascade delete the threads as well."""
    propagate_soft_delete(mapper, connection, target,
                          "tagitems", "tag_id", "id")
