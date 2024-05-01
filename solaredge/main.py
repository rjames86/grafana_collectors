#!./venv/bin/python3
# -*- coding: utf-8 -*-

import argparse
import example_data
import requests
import secrets
import pytz
from collections import defaultdict

from datetime import datetime, timedelta
from solaredge import Solaredge

# update this to your local timezone
# solaredge timestamps are in the time zone of the site
SE_TIMEZONE = pytz.timezone("America/Denver")

SE_FMT_DATE = '%Y-%m-%d'
SE_FMT_DATETIME = '%Y-%m-%d %H:%M:%S'

IDB_DATABASE = "solar_edge"

# this is the timezone used to store the data in InfluxDB. UTC is usually a good choice.
IDB_TIMEZONE = pytz.utc
IDB_FMT = '%Y-%m-%dT%H:%M:%SZ'


class InfluxKeys:
    def __init__(self, measurement, field='value'):
        self.measurement = 'sensor__%s' % measurement
        self.tags = {
            'entity_id': 'solaredge_%s' % measurement,
            'domain': 'sensor',
        }
        self.field = field


power_measurements_to_keys = dict(
    Production=InfluxKeys('power_production'),
    Consumption=InfluxKeys('power_consumption'),
    SelfConsumption=InfluxKeys('power_self_consumption'),
    FeedIn=InfluxKeys('power_feedin'),
    Purchased=InfluxKeys('power_import'),
)

energy_measurements_to_keys = dict(
    Production=InfluxKeys('energy_production'),
    Consumption=InfluxKeys('energy_consumption'),
    SelfConsumption=InfluxKeys('energy_self_consumption'),
    FeedIn=InfluxKeys('energy_feedin'),
    Purchased=InfluxKeys('energy_import'),
)


def _parse_input_timestamp(timestamp: str) -> datetime:
    try:
        return datetime.strptime(timestamp, SE_FMT_DATE)
    except ValueError:
        return datetime.strptime(timestamp, SE_FMT_DATETIME)


def _parse_solaredge_timestamp(timestamp: str) -> datetime:
    dt = datetime.strptime(timestamp, SE_FMT_DATETIME)
    dt_local = SE_TIMEZONE.localize(dt)
    return dt_local.astimezone(IDB_TIMEZONE)

def date_in_local_timezone(dt: datetime) -> datetime:
    return dt.astimezone(SE_TIMEZONE)


def _format_timestamp(dt: datetime, fmt: str) -> str:
    return dt.strftime(fmt)


def pull_current_power_data(client: Solaredge, begin: datetime, end: datetime):
    return client.get_power(secrets.solaredge_site_id,
                            _format_timestamp(begin, SE_FMT_DATETIME),
                            _format_timestamp(end, SE_FMT_DATETIME))


def pull_power_details_data(client: Solaredge, begin: datetime, end: datetime):
    return client.get_power_details(secrets.solaredge_site_id,
                                    _format_timestamp(begin, SE_FMT_DATETIME),
                                    _format_timestamp(end, SE_FMT_DATETIME))


def pull_energy_data(client: Solaredge, begin: datetime, end: datetime, timeunit: str):
    return client.get_energy(secrets.solaredge_site_id,
                             _format_timestamp(begin, SE_FMT_DATE),
                             _format_timestamp(end, SE_FMT_DATE),
                             time_unit=timeunit)


def pull_energy_details_data(client: Solaredge, begin: datetime, end: datetime, timeunit: str):
    return client.get_energy_details(secrets.solaredge_site_id,
                                     _format_timestamp(begin, SE_FMT_DATETIME),
                                     _format_timestamp(end, SE_FMT_DATETIME),
                                     None,
                                     time_unit=timeunit)


def parse_details_data(details, detail_key):
    data_points = defaultdict(list)

    for meter in details[detail_key]['meters']:
        meter_type = meter['type']

        for value in meter['values']:
            if value.get('value') is None:
                continue

            data_points[meter_type].append({
                'timestamp': _parse_solaredge_timestamp(value['date']),
                'value': value['value']
            })
    return data_points


def parse_energy_data(energy_data):
    data_points = []

    for ed in energy_data['energy']['values']:
        if ed['value'] is None:
            continue

        data_points.append({
            'timestamp': _parse_solaredge_timestamp(ed['date']),
            'value': ed['value']
        })
    return data_points


def parse_current_power_data(power_data):
    data_points = []
    for pd in power_data['power']['values']:
        if pd['value'] is None:
            continue

        data_points.append({
            'timestamp': _parse_solaredge_timestamp(pd['date']),
            'value': pd['value']
        })

    return data_points


def write_data(data, measurement, tags, field_name, verbose):
    data_points = []
    for d in data:

        local_dt = date_in_local_timezone(d['timestamp'])
        month, year = local_dt.month, local_dt.year

        tags['year'] = year
        tags['month'] = month

        dp = {
            "measurement": measurement,
            "tags": tags,
            "time": _format_timestamp(d['timestamp'], IDB_FMT),
            "fields": {
                field_name: d['value']
            }
        }
        if verbose:
            print(dp)

        data_points.append(dp)

    requests.post('http://api:5000/influx/solar_edge/write', json=dict(data_points=data_points))


def main():
    default_begin = (datetime.now() - timedelta(days=14)).strftime('%Y-%m-%d')

    parser = argparse.ArgumentParser(
        description='Pull data from the SolarEdge API and store it into an InfluxDB database.')
    parser.add_argument("--begin", type=str, default=default_begin,
                        help="Begin timestamp in the format YYYY-MM-DD[ hh:mm:ss]")
    parser.add_argument("--end", type=str, default=None,
                        help="End timestamp in the format YYYY-MM-DD[ hh:mm:ss]")
    parser.add_argument("-p", "--power", action='store_true',
                        help="Include current power data")
    parser.add_argument("-e", "--energy", action='store_true',
                        help="Include energy data")
    parser.add_argument("-g", "--granularity", default='QUARTER_OF_AN_HOUR', help="Granularity for energy data",
                        choices=['QUARTER_OF_AN_HOUR', 'HOUR', 'DAY', 'WEEK'])
    parser.add_argument("-v", "--verbose",
                        action='store_true', help="Verbose output")
    parser.add_argument("-d", "--dry-run", action='store_true', help="Don't pull any actual data from the solaredge api,"
                                                                     " use example data instead")
    args = parser.parse_args()

    begin = _parse_input_timestamp(args.begin)
    end = _parse_input_timestamp(args.end) if args.end else datetime.now()

    solaredge_client = Solaredge(secrets.solaredge_token)

    energy_details_data = pull_energy_details_data(
        solaredge_client, begin, end, args.granularity)
    # if args.verbose:
    #     print("Raw energy details data:")
    #     print(energy_details_data)
    energy_details = parse_details_data(energy_details_data, 'energyDetails')
    print("got {} energy data points".format(len(energy_details)))

    # if args.verbose:
    #     print("Parsed energy details:")
    #     print(energy_details)
    #     print("writing energy details")

    for meter_type, data in energy_details.items():
        influx_data = energy_measurements_to_keys[meter_type]
        write_data(
            data,
            influx_data.measurement,
            influx_data.tags,
            influx_data.field,
            args.verbose)

    power_details_data = pull_power_details_data(solaredge_client, begin, end)
    # if args.verbose:
    #     print("Raw power details data:")
    #     print(power_details_data)
    power_details = parse_details_data(power_details_data, 'powerDetails')
    print("got {} power data points".format(len(power_details)))

    # if args.verbose:
    #     print("Parsed power details:")
    #     print(power_details)
    #     print("writing power details")

    for meter_type, data in power_details.items():
        influx_data = power_measurements_to_keys[meter_type]
        write_data(
            data,
            influx_data.measurement,
            influx_data.tags,
            influx_data.field,
            args.verbose)


if __name__ == '__main__':
    main()
