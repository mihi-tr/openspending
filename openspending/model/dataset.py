"""
The ``Dataset`` serves as double function in OpenSpending: on one hand, it is
a simple domain object that can be created, modified and deleted as any other
On the other hand it serves as a controller object for the dataset-specific
data model which it represents, handling the creation, filling and migration of
the table schema associated with the dataset. As such, it holds the key set
of logic functions upon which all other queries and loading functions rely.
"""
import math
import logging
from collections import defaultdict
from datetime import datetime
from itertools import count
from sqlalchemy import ForeignKeyConstraint

from openspending.model import meta as db
from openspending.lib.util import hash_values

from openspending.model.common import JSONType
from openspending.store.cube import Cube

log = logging.getLogger(__name__)


class Dataset(db.Model):
    """ The dataset is the core entity of any access to data. All
    requests to the actual data store are routed through it, as well
    as data loading and model generation.

    The dataset keeps an in-memory representation of the data model
    (including all dimensions and measures) which can be used to
    generate necessary queries.
    """
    __tablename__ = 'dataset'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.Unicode(255), unique=True)
    label = db.Column(db.Unicode(2000))
    description = db.Column(db.Unicode())
    currency = db.Column(db.Unicode())
    default_time = db.Column(db.Unicode())
    schema_version = db.Column(db.Unicode())
    entry_custom_html = db.Column(db.Unicode())
    ckan_uri = db.Column(db.Unicode())
    category = db.Column(db.Unicode())
    serp_title = db.Column(db.Unicode(), nullable=True)
    serp_teaser = db.Column(db.Unicode(), nullable=True)
    private = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)
    data = db.Column(JSONType, default=dict)

    languages = db.association_proxy('_languages', 'code')
    territories = db.association_proxy('_territories', 'code')

    def __init__(self, data):
        self.data = data.copy()
        self.cube= Cube(self.data.copy())
        dataset = self.data['dataset']
        del self.data['dataset']
        self.label = dataset.get('label')
        self.name = dataset.get('name')
        self.description = dataset.get('description')
        self.currency = dataset.get('currency')
        self.category = dataset.get('category')
        self.serp_title = dataset.get('serp_title')
        self.serp_teaser = dataset.get('serp_teaser')
        self.default_time = dataset.get('default_time')
        self.entry_custom_html = dataset.get('entry_custom_html')
        self.languages = dataset.get('languages', [])
        self.territories = dataset.get('territories', [])
        self.ckan_uri = dataset.get('ckan_uri')
        self._load_model()

    @property
    def model(self):
        model = self.data.copy()
        model['dataset'] = self.as_dict()
        return model

    @property
    def mapping(self):
        return self.data.get('mapping', {})

    @db.reconstructor
    def _load_model(self):
        """ Construct the in-memory object representation of this
        dataset's dimension and measures model.

        This is called upon initialization and deserialization of
        the dataset from the SQLAlchemy store.
        """
        self.cube._load_model()

    def __getitem__(self, name):
        """ Access a field (dimension or measure) by name. """
        return self.cube.__getitem__(name)

    def __contains__(self, name):
        return self.cube.__contains(name)

    @property
    def fields(self):
        """ Both the dimensions and metrics in this dataset. """
        return self.cube.fields()

    @property
    def compounds(self):
        """ Return only compound dimensions. """
        return self.cube.compounds()

    @property
    def facet_dimensions(self):
        return self.cube.facet_dimensions()

    def init(self):
        """ Create a SQLAlchemy model for the current dataset model,
        without creating the tables and columns. This needs to be
        called both for access to the data and in order to generate
        the model physically. """
        self.bind = db.engine
        self.meta = db.MetaData()
        #self.tx = self.bind.begin()
        self.meta.bind = db.engine

        self._init_table(self.meta, self.name, 'entry',
                         id_type=db.Unicode(42))
        for field in self.fields:
            field.column = field.init(self.meta, self.table)
        self.alias = self.table.alias('entry')

    def generate(self):
        """ Create the tables and columns necessary for this dataset
        to keep data.
        """
        return self.cube.generate()

    @property
    def is_generated(self):
        return self.cube._is_generated

    def commit(self):
        pass
        #self.tx.commit()
        #self.tx = self.bind.begin()

    def _make_key(self, data):
        """ Generate a unique identifier for an entry. This is better
        than SQL auto-increment because it is stable across mutltiple
        loads and thus creates stable URIs for entries.
        """
        return self.cube._make_key(data)

    def load(self, data):
        """ Handle a single entry of data in the mapping source format,
        i.e. with all needed columns. This will propagate to all dimensions
        and set values as appropriate. """
        return self.cube.load(data)

    def flush(self):
        """ Delete all data from the dataset tables but leave the table
        structure intact.
        """
        self.cube.flush()

    def drop(self):
        """ Drop all tables created as part of this dataset, i.e. by calling
        ``generate()``. This will of course also delete the data itself.
        """
        self.cube.drop()

    def key(self, key):
        """ For a given ``key``, find a column to indentify it in a query.
        A ``key`` is either the name of a simple attribute (e.g. ``time``)
        or of an attribute of a complex dimension (e.g. ``to.label``). The
        returned key is using an alias, so it can be used in a query
        directly. """
        return self.cube.key(key)

    def entries(self, *args, **kwargs):
        """ Generate a fully denormalized view of the entries on this
        table. This view is nested so that each dimension will be a hash
        of its attributes.

        This is somewhat similar to the entries collection in the fully
        denormalized schema before OpenSpending 0.11 (MongoDB).
        """
        return self.cube.entries(*args, **kwargs)

    def aggregate(self, *args, **kwargs):
        return self.cube.aggregate(*args, **kwargs)

    def __repr__(self):
        return "<Dataset(%s:%s:%s)>" % (self.name, self.dimensions,
                self.measures)

    def __len__(self):
        if not self.is_generated:
            return 0
        rp = self.bind.execute(self.alias.count())
        return rp.fetchone()[0]

    def as_dict(self):
        return {
            'label': self.label,
            'name': self.name,
            'description': self.description,
            'default_time': self.default_time,
            'schema_version': self.schema_version,
            'currency': self.currency,
            'category': self.category,
            'serp_title': self.serp_title,
            'serp_teaser': self.serp_teaser,
            'languages': list(self.languages),
            'territories': list(self.territories)
            }

    @classmethod
    def all_by_account(cls, account):
        """ Query available datasets based on dataset visibility. """
        criteria = [cls.private == False]
        if account is not None:
            criteria += ["1=1" if account.admin else "1=2",
                         cls.managers.any(type(account).id == account.id)]
        q = db.session.query(cls).filter(db.or_(*criteria))
        q = q.order_by(cls.label.asc())
        return q

    @classmethod
    def by_name(cls, name):
        return db.session.query(cls).filter_by(name=name).first()

