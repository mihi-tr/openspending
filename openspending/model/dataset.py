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
        entry = dict()
        for field in self.fields:
            field_data = data[field.name]
            entry.update(field.load(self.bind, field_data))
        entry['id'] = self._make_key(data)
        self._upsert(self.bind, entry, ['id'])

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

    def aggregate(self, measure='amount', drilldowns=None, cuts=None,
            page=1, pagesize=10000, order=None):
        """ Query the dataset for a subset of cells based on cuts and
        drilldowns. It returns a structure with a list of drilldown items
        and a summary about the slice cutted by the query.

        ``measure``
            The numeric unit to be aggregated over, defaults to ``amount``.
        ``drilldowns``
            Dimensions to drill down to. (type: `list`)
        ``cuts``
            Specification what to cut from the cube. This is a
            `list` of `two-tuples` where the first item is the dimension
            and the second item is the value to cut from. It is turned into
            a query where multible cuts for the same dimension are combined
            to an *OR* query and then the queries for the different
            dimensions are combined to an *AND* query.
        ``page``
            Page the drilldown result and return page number *page*.
            type: `int`
        ``pagesize``
            Page the drilldown result into page of size *pagesize*.
            type: `int`
        ``order``
            Sort the result based on the dimension *sort_dimension*.
            This may be `None` (*default*) or a `list` of two-`tuples`
            where the first element is the *dimension* and the second
            element is the order (`False` for ascending, `True` for
            descending).
            Type: `list` of two-`tuples`.

        Raises:

        :exc:`ValueError`
            If a cube is not yet computed. Call :meth:`compute` to compute
            the cube.
        :exc:`KeyError`
            If a drilldown, cut or order dimension is not part of this
            cube or the order dimensions are not a subset of the drilldown
            dimensions.

        Returns: A `dict` containing the drilldown and the summary::

          {"drilldown": [
              {"num_entries": 5545,
               "amount": 41087379002.0,
               "cofog1": {"description": "",
                          "label": "Economic affairs"}},
              ... ]
           "summary": {"amount": 7353306450299.0,
                       "num_entries": 133612}}

        """
        cuts = cuts or []
        drilldowns = drilldowns or []
        order = order or []
        joins = alias = self.alias
        dataset = self
        fields = [db.func.sum(alias.c[measure]).label(measure),
                  db.func.count(alias.c.id).label("entries")]
        stats_fields = list(fields)
        labels = {
            'year': dataset['time']['year'].column_alias.label('year'),
            'month': dataset['time']['yearmonth'].column_alias.label('month'),
            }
        dimensions = drilldowns + [k for k, v in cuts]
        dimensions = [d.split('.')[0] for d in dimensions]
        for dimension in set(dimensions):
            if dimension in labels:
                dimension = 'time'
            if dimension not in [c.table.name for c in joins.columns]:
                joins = dataset[dimension].join(joins)

        group_by = []
        for key in drilldowns:
            if key in labels:
                column = labels[key]
                group_by.append(column)
                fields.append(column)
            else:
                column = dataset.key(key)
                if '.' in key or column.table == alias:
                    fields.append(column)
                    group_by.append(column)
                else:
                    fields.append(column.table)
                    for col in column.table.columns:
                        group_by.append(col)

        conditions = db.and_()
        filters = defaultdict(set)
        for key, value in cuts:
            if key in labels:
                column = labels[key]
            else:
                column = dataset.key(key)
            filters[column].add(value)
        for attr, values in filters.items():
            conditions.append(db.or_(*[attr == v for v in values]))

        order_by = []
        if order is None or not len(order):
            order = [(measure, True)]
        for key, direction in order:
            if key == measure:
                column = db.func.sum(alias.c[measure]).label(measure)
            elif key in labels:
                column = labels[key]
            else:
                column = dataset.key(key)
            order_by.append(column.desc() if direction else column.asc())

        # query 1: get overall sums.
        query = db.select(stats_fields, conditions, joins)
        rp = dataset.bind.execute(query)
        total, num_entries = rp.fetchone()

        # query 2: get total count of drilldowns
        if len(group_by):
            query = db.select(['1'], conditions, joins, group_by=group_by)
            query = db.select([db.func.count('1')], '1=1', query.alias('q'))
            rp = dataset.bind.execute(query)
            num_drilldowns, = rp.fetchone()
        else:
            num_drilldowns = 1

        drilldown = []
        offset = int((page - 1) * pagesize)

        # query 3: get the actual data
        query = db.select(fields, conditions, joins, order_by=order_by,
                          group_by=group_by, use_labels=True,
                          limit=pagesize, offset=offset)
        rp = dataset.bind.execute(query)
        while True:
            row = rp.fetchone()
            if row is None:
                break
            result = decode_row(row, dataset)
            drilldown.append(result)

        return {
                'drilldown': drilldown,
                'summary': {
                    measure: total,
                    'num_entries': num_entries,
                    'currency': {measure: dataset.currency},
                    'num_drilldowns': num_drilldowns,
                    'page': page,
                    'pages': int(math.ceil(num_drilldowns / float(pagesize))),
                    'pagesize': pagesize
                    }
                }

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

