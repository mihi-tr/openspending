"""The application's model objects"""
from sqlalchemy import orm

from openspending.store import meta as db
from openspending.model.attribute import Attribute
from openspending.model.dimension import AttributeDimension, \
        CompoundDimension, Measure, Dimension, DateDimension

# shut up useless SA warning:
import warnings
warnings.filterwarnings('ignore', 'Unicode type received non-unicode bind param value.')


def init_model(engine):
    """ Initialize the SQLAlchemy driver and session maker. """
    #if db.session is not None:
    #    return
    sm = orm.sessionmaker(autoflush=True, bind=engine)
    db.engine = engine
    db.metadata.bind = engine
    db.session = orm.scoped_session(sm)
