# -*- coding: utf-8 -*-
#
# This file is part of RERO MEF.
# Copyright (C) 2018 RERO.
#
# RERO MEF is free software; you can redistribute it
# and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 2 of the
# License, or (at your option) any later version.
#
# RERO MEF is distributed in the hope that it will be
# useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with RERO MEF; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place, Suite 330, Boston,
# MA 02111-1307, USA.
#
# In applying this license, RERO does not
# waive the privileges and immunities granted to it by virtue of its status
# as an Intergovernmental Organization or submit itself to any jurisdiction.

"""Utils for authorities."""

import hashlib
import json
import os
import re
from datetime import datetime
from uuid import uuid4

import ijson
import psycopg2
from flask import current_app
from sqlalchemy import create_engine

from .api import MefRecord, ViafRecord


def metadata_csv_line(record, record_uuid, date):
    """Build CSV metadata table line."""
    created_date = updated_date = date
    sep = '\t'
    metadata = (
        created_date,
        updated_date,
        record_uuid,
        json.dumps(record).replace('\\', '\\\\'),
        '1',
    )
    metadata_line = sep.join(metadata)
    return metadata_line + os.linesep


def pidstore_csv_line(agency, agency_pid, record_uuid, date):
    """Build CSV pidstore table line."""
    created_date = updated_date = date
    sep = '\t'
    pidstore_data = [
        created_date,
        updated_date,
        agency,
        agency_pid,
        'R',
        'rec',
        record_uuid,
    ]
    pidstore_line = sep.join(pidstore_data)
    return pidstore_line + os.linesep


def add_agency_to_json(mef_record, agency, agency_pid):
    """Add agency ref to mef record."""
    ref_string = MefRecord.build_ref_string(
        agency=agency, agency_pid=agency_pid
    )
    mef_record[agency] = {'$ref': ref_string}


def create_mef_csv_file(pidstore, metadata):
    """Create CSV MEF file."""
    mef_pid = 0
    with open(metadata, 'w', encoding='utf-8') as agency_metadata_file, \
            open(pidstore, 'w', encoding='utf-8') as agency_pids_file:

        for viaf_pid in ViafRecord.get_all_pids():
            viaf_record = ViafRecord.get_record_by_pid(viaf_pid)
            mef_record = {}
            with current_app.app_context():
                agencies = current_app.config.get('AGENCIES')
                for agency in agencies:
                    agency_pid = '{agency}_pid'.format(agency=agency)
                    agency_viaf_pid = viaf_record.get(agency_pid, '')
                    if agency_viaf_pid:
                        agency_record = agencies[agency].get_record_by_pid(
                            agency_viaf_pid
                        )
                        if agency_record:
                            add_agency_to_json(
                                mef_record, agency, agency_viaf_pid)

            if len(mef_record):
                mef_pid = mef_pid + 1
                mef_record['pid'] = mef_pid
                mef_record['viaf_pid'] = viaf_pid
                add_schema(mef_record, 'mef')

                record_uuid = str(uuid4())
                date = str(datetime.utcnow())

                agency_metadata_file.write(
                    metadata_csv_line(mef_record, record_uuid, date)
                )
                agency_pids_file.write(
                    pidstore_csv_line('mef', str(mef_pid), record_uuid, date)
                )


def raw_connection():
    """Return a raw connection to the database."""
    with current_app.app_context():
        URI = current_app.config.get('SQLALCHEMY_DATABASE_URI')
        engine = create_engine(URI)
        # conn = engine.connect()
        connection = engine.raw_connection()
        return connection


def bulk_load_agency_metadata(agency, metadata):
    """Bulk load agency data to metadata table."""
    connection = raw_connection()
    cur = connection.cursor()
    with open(metadata, 'r', encoding='utf-8') as input_file:
        try:
            cur.copy_from(
                file=input_file,
                table='records_metadata',
                columns=('created', 'updated', 'id', 'json', 'version_id'),
                sep='\t',
            )
            connection.commit()
        except psycopg2.DataError as error:
            current_app.logger.error(
                'data load error: {0}'.format(error)
            )
    cur.close()
    connection.close()


def bulk_load_agency_pids(agency, pidstore):
    """Bulk load agency data to pidstore table."""
    connection = raw_connection()
    cur = connection.cursor()
    with open(pidstore, 'r', encoding='utf-8') as input_file:
        try:
            cur.copy_from(
                file=input_file,
                table='pidstore_pid',
                columns=(
                    'created',
                    'updated',
                    'pid_type',
                    'pid_value',
                    'status',
                    'object_type',
                    'object_uuid',
                ),
                sep='\t',
            )
            connection.commit()
        except psycopg2.DataError as error:
            current_app.logger.error(
                'data load error: {0}'.format(error)
            )
    cur.close()
    connection.close()


def add_md5_to_json(record):
    """Add md5 to json."""
    data_md5 = hashlib.md5(
        json.dumps(record, sort_keys=True).encode('utf-8')
    ).hexdigest()
    record['md5'] = data_md5
    return record


def add_schema(record, agency):
    """Add the $schema to the record."""
    with current_app.app_context():
        s_data = {
            'http': 'http://',
            'url': current_app.config.get('JSONSCHEMAS_HOST'),
            'schema': '/schemas/authorities/',
            'agency': agency,
            'suffix': '-person-v0.0.1.json',
        }
        schema_str = '{http}{url}{schema}{agency}{suffix}'.format(**s_data)
        record['$schema'] = schema_str


def create_agency_csv_file(input_file, agency, pidstore, metadata):
    """Create agency csv file to load."""
    with \
            open(input_file, 'r', encoding='utf-8') as agency_file, \
            open(metadata, 'w', encoding='utf-8') as agency_metadata_file, \
            open(pidstore, 'w', encoding='utf-8') as agency_pids_file:

        agency_key = '{agency}_pid'.format(agency=agency)

        for record in ijson.items(agency_file, "item"):
            if agency == 'viaf':
                record['pid'] = record[agency_key]
            else:
                record[agency_key] = record['pid']

            agency_pid = record[agency_key]

            ordered_record = add_md5_to_json(record)
            add_schema(ordered_record, agency)

            record_uuid = str(uuid4())
            date = str(datetime.utcnow())

            agency_metadata_file.write(
                metadata_csv_line(ordered_record, record_uuid, date)
            )

            agency_pids_file.write(
                pidstore_csv_line(agency, agency_pid, record_uuid, date)
            )


def create_csv_agency_file(
    agency_input_file, agency, pidstore_file, metadata_file
):
    """Create agency csv file to load."""
    agency_key = '{agency}_pid'.format(agency=agency)
    with open(metadata_file, 'w', encoding='utf-8') as agency_metadata_file:

        with open(
            pidstore_file, 'w', encoding='utf-8'
        ) as agency_pidstore_file:

            with open(
                str(agency_input_file), 'r', encoding='utf-8'
            ) as agency_file:
                mef_pid = 0
                for record in ijson.items(agency_file, 'item'):
                    # TODO: make sure pid, agnecy_pid exist in all input files
                    if agency == 'viaf':
                        record['pid'] = record[agency_key]
                    elif agency == 'mef':
                        # record['pid'] = record[agency_key]
                        record['pid'] = record['viaf_pid']
                        mef_pid += 1
                        record[agency_key] = mef_pid
                    else:
                        record[agency_key] = record['pid']
                    agency_pid = record[agency_key]
                    sorted_record = add_md5_to_json(record)
                    add_schema(sorted_record, agency)
                    record_uuid = str(uuid4())
                    date = str(datetime.utcnow())

                    agency_metadata_file.write(
                        metadata_csv_line(sorted_record, record_uuid, date)
                    )
                    agency_pidstore_file.write(
                        pidstore_csv_line(
                            agency, str(agency_pid), record_uuid, date
                        )
                    )


def viaf_to_mef(viaf_record):
    """Transform viaf recod to mef."""
    mef_record = {}
    with current_app.app_context():
        agencies = current_app.config.get('AGENCIES')
        agency_record = viaf_record
        del agency_record['viaf_pid']
        for key in agency_record:
            agency = key[:-4]
            if agencies[agency].get_record_by_pid(agency_record[key]):
                add_agency_to_json(mef_record, agency, agency_record[key])
        if len(mef_record):
            add_schema(mef_record, 'mef')
            mef_record['viaf_pid'] = viaf_record[key]
        return mef_record


def write_link_json(
    agency,
    pidstore_file,
    metadata_file,
    viaf_pid,
    corresponding_data,
    agency_pid
):
    """Write a json record into file."""
    json_data = {}
    key_per_catalog_id = {
        'BNF|': 'bnf_pid',
        'BNF@': 'bnf_uri',
        'DNB|': 'gnd_pid',
        'DNB@': 'gnd_uri',
        'RERO': 'rero_pid',
    }
    json_data['viaf_pid'] = viaf_pid
    for catalog_id in corresponding_data:
        if catalog_id != 'BNF@' and catalog_id != 'DNB@':
            json_data[key_per_catalog_id[catalog_id]] = corresponding_data[
                catalog_id
            ]
    write_to_file = False
    json_dump = json_data
    if agency == 'mef':
        json_dump = viaf_to_mef(json_data)
        if json_dump:
            json_dump['pid'] = agency_pid
            write_to_file = True
    else:
        agency_pid = viaf_pid
        add_schema(json_dump, 'viaf')
        json_dump['pid'] = agency_pid
        write_to_file = True

    if write_to_file:
        record_uuid = str(uuid4())
        date = str(datetime.utcnow())
        pidstore_file.write(
            pidstore_csv_line(agency, agency_pid, record_uuid, date)
        )
        metadata_file.write(metadata_csv_line(json_dump, record_uuid, date))


def create_viaf_mef_files(
    agency,
    rero_pids_file,
    viaf_input_file,
    agency_pidstore_file_name,
    agency_metadata_file_name,
):
    """Create agency csv file to load."""
    with open(rero_pids_file, 'r', encoding='utf-8') as rero_pids:
        rero_id_control_number = {}
        for line in rero_pids:
            parts = line.rstrip().split('\t')
            rero_id_control_number[parts[0]] = parts[1]

    previous_viaf_pid = None
    agency_pid = 0
    corresponding_data = {}
    with open(
        agency_pidstore_file_name, 'w', encoding='utf-8'
    ) as agency_pidstore:
        with open(
            agency_metadata_file_name, 'w', encoding='utf-8'
        ) as agency_metadata:
            with open(
                str(viaf_input_file), 'r', encoding='utf-8'
            ) as viaf_in_file:
                for row in viaf_in_file:
                    agency_pid += 1
                    fields = re.split('\t', row.rstrip())
                    assert len(fields) == 2
                    viaf_pid = fields[0]
                    if viaf_pid != previous_viaf_pid and previous_viaf_pid:
                        write_link_json(
                            agency,
                            agency_pidstore,
                            agency_metadata,
                            previous_viaf_pid,
                            corresponding_data,
                            str(agency_pid)
                        )
                        corresponding_data = {}
                    corresponding_str = fields[1]
                    if re.match('BNF|DNB', corresponding_str):
                        corresponding_data[
                            corresponding_str[0:4]
                        ] = corresponding_str[4:]
                    elif re.match('RERO', corresponding_str):
                        virtua_auth_id = corresponding_str[5:]
                        corresponding_data['VIRTUA'] = virtua_auth_id
                        corresponding_data = {}
                        if virtua_auth_id in rero_id_control_number:
                            corresponding_data[
                                'RERO'
                            ] = rero_id_control_number[virtua_auth_id]
                    previous_viaf_pid = viaf_pid
                write_link_json(
                    agency,
                    agency_pidstore,
                    agency_metadata,
                    previous_viaf_pid,
                    corresponding_data,
                    str(agency_pid)
                )
