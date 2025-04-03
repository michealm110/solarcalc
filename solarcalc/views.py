import flask
import sqlite3
import hashids
import requests
import pvlib
import pandas
from datetime import datetime, timedelta, timezone
import pytz
import os
from werkzeug.utils import secure_filename
import numpy
import json

UPLOAD_FOLDER = "/Users/michealmcmagh/Desktop/ise-entrance-submission-project/solarcalc/uploads"
ALLOWED_EXTENSIONS = {"csv"}

def db_get_connection():
    con = sqlite3.connect("solarcalc.db")
    con.execute("CREATE TABLE IF NOT EXISTS simulation (id INTEGER PRIMARY KEY AUTOINCREMENT, eircode TEXT NOT NULL , latitude_longitude TEXT, rated_power_per_panel REAL, number_of_panels INTEGER, panel_tilt REAL, panel_azimuth REAL);")
    con.commit()
    return con

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

app = flask.Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


@app.route("/", methods=["GET", "POST"])
def index():
    if flask.request.method == "POST":

        # Getting and storing all the inputs inputted on index.html
        eircode = flask.request.form.get("eircode")   
        lat_lon = get_lat_lon_from_eircode(eircode)
        lat_lon_string = ",".join(map(str, lat_lon))

        # Save all these inputs to the solcalc.db database
        con = db_get_connection()
        cur = con.cursor()
        cur.execute("INSERT INTO simulation (eircode , latitude_longitude) VALUES (?,?)", (eircode, lat_lon_string))
        last_row_id = cur.lastrowid
        con.commit()
        con.close()

        hash_id = encode_id(last_row_id)
        return flask.redirect(flask.url_for("get_detailed_user_data", hash_id=hash_id))

    elif flask.request.method == "GET":
        return flask.render_template("index.html")
    else:
        raise


@app.route("/<hash_id>", methods=["GET", "POST"])
def get_detailed_user_data(hash_id):
    if flask.request.method == "POST":
        decoded_id = decode_id(hash_id)
        rated_power_per_panel = flask.request.form.get("rated_power_per_panel")
        number_of_panels = flask.request.form.get("number_of_panels")
        panel_tilt = flask.request.form.get("panel_tilt")
        panel_azimuth = flask.request.form.get("panel_azimuth")


        # Update data base with new inputs
        con = db_get_connection()
        cur = con.cursor()
        cur.execute("UPDATE simulation SET rated_power_per_panel = ?, number_of_panels = ?, panel_tilt = ?, panel_azimuth = ? WHERE id = ?", (rated_power_per_panel, number_of_panels, panel_tilt, panel_azimuth, decoded_id))   
        con.commit()
        con.close()

        return flask.render_template("furtherdetails.html", hash_id=hash_id)
    elif flask.request.method == "GET":
        con = db_get_connection()
        cur = con.cursor()
        cur.execute("SELECT * FROM simulation WHERE id = ?", (hash_id,))
        row = cur.fetchone()
        # row is a tuple or "None"
        con.close()

        if row is not None:
            return flask.render_template("furtherdetails.html", hash_id=hash_id, rated_power_per_panela=row[3], number_of_panelsy=row[4], panel_tilt=row[5], panel_azimuth=row[6])
        
        return flask.render_template("furtherdetails.html", hash_id=hash_id, rated_power_per_panel=445, number_of_panels=1, panel_azimuth=180, panel_tilt=18)
    else:
        raise

    
@app.route("/<hash_id>/solardata")
def get_json_data(hash_id):
    decoded_id = decode_id(hash_id)
    # times_from_now = rounds_and_calculates_a_year_of_dates()
    # Get data from solarcalc.db database and plug the data into calc_power_output() 
    con = db_get_connection()
    cur = con.cursor()
    # Not worth individually assiging each parameter grabbing them all as a tuple
    cur.execute("SELECT * FROM simulation WHERE id = ?", (decoded_id,))
    decoded_id_row_from_db = cur.fetchone()
    con.close()

    # Converting the latitude_longitude to a tuple
    lat_lon = tuple(map(float, decoded_id_row_from_db[2].split(",")))\
    
 
    dc_power_output = calc_power_output(lat_lon[0], lat_lon[1], decoded_id_row_from_db[3], decoded_id_row_from_db[4], decoded_id_row_from_db[5], decoded_id_row_from_db[6])
    dc_power_output = dc_power_output.reset_index().rename(columns={"time(UTC)": "x", 0: "y"})

    times = pandas.date_range(start="2024-01-01 00:30:00.000", end="2024-12-31 00:00:00.000", freq="1h", tz="UTC")

    new_rows = pandas.DataFrame({"x": times, "y": numpy.nan})
        
    dc_power_output = pandas.concat([dc_power_output, new_rows], ignore_index=True)

    dc_power_output = dc_power_output.sort_values(by="x").reset_index(drop=True)

    if numpy.isnan(dc_power_output.loc[0, "y"]):
        dc_power_output.loc[0, "y"] = 0

    if numpy.isnan(dc_power_output.loc[len(dc_power_output) - 1, "y"]):
        dc_power_output.loc[len(dc_power_output) - 1, "y"] = 0

    dc_power_output["y"] = get_avg_value(dc_power_output["y"].to_list())

    json_power_output = dc_power_output.to_json(orient="records", date_format="iso")
    return json_power_output

@app.route("/<hash_id>/esbdata")
def get_esb_json_data(hash_id):
    esb_intake = pandas.read_csv(f"/Users/michealmcmagh/Desktop/ise-entrance-submission-project/solarcalc/uploads/{hash_id}.csv")
    esb_intake = pandas.concat([esb_intake["Read Date and End Time"], esb_intake["Read Value"]], join="inner", axis=1)
    esb_intake = esb_intake.rename(columns={"Read Date and End Time": "x", "Read Value": "y"})
    # Converts killowatts to watts
    esb_intake["y"] = esb_intake["y"] * 1000
    esb_intake["x"] = pandas.to_datetime(esb_intake["x"], dayfirst=True).dt.tz_localize('UTC')
    esb_intake = esb_intake.sort_values(by=['x'])

    json_esb_intake = esb_intake.to_json(orient='records', date_format='iso')

    return json_esb_intake

@app.route("/<hash_id>/full_combined_data")
def get_combined_json_data(hash_id):
    solar_json_data = get_json_data(hash_id=hash_id)
    esb_json_data = get_esb_json_data(hash_id=hash_id)
    solar_data = pandas.DataFrame.from_records(json.loads(solar_json_data))
    esb_data = pandas.DataFrame.from_records(json.loads(esb_json_data))
    solar_data["x"] = pandas.to_datetime(solar_data["x"])
    esb_data["x"] = pandas.to_datetime(esb_data["x"])
    start = pandas.Timestamp("2024-01-01 00:00:00", tz="UTC")
    end = pandas.Timestamp("2024-12-31 23:59:59", tz="UTC")
    solar_data = solar_data[(solar_data["x"] >= start) & (solar_data["x"] <= end)]
    esb_data = esb_data[(esb_data["x"] >= start) & (esb_data["x"] <= end)]
    combined_data = pandas.merge(solar_data.rename(columns={"y": "y1"}), esb_data.rename(columns={"y": "y2"}), on="x", how="outer")
    combined_data_json = combined_data.to_json(orient="records", date_format="iso")

    return combined_data_json

@app.route("/<hash_id>/combineddata", methods=["GET"])
def get_combined_json_data_for_simulator(hash_id):
    combined_json_data = get_combined_json_data(hash_id=hash_id)
    combined_data = pandas.DataFrame.from_records(json.loads(combined_json_data))
    start = flask.request.args.get("start", "2024-")
    start = pandas.to_datetime(start).tz_localize("UTC")
    end = start + pandas.Timedelta(hours=23, minutes=30)    
    combined_data["x"] = pandas.to_datetime(combined_data["x"]) 
    combined_data = combined_data[(combined_data["x"] >= start) & (combined_data["x"] < end)]

    combined_json = combined_data.to_json(orient="records", date_format="iso")

    return combined_json

    

@app.route("/<hash_id>/simulate")
def simulate(hash_id):
    return flask.render_template("simulator.html", hash_id=hash_id)

@app.route("/<hash_id>/excess_energy")   
def  get_excess_json_data(hash_id):
    combined_json_data = get_combined_json_data(hash_id=hash_id)
    combined_data = pandas.DataFrame.from_records(json.loads(combined_json_data))
    start = flask.request.args.get("start", "2024-01-01")
    start = pandas.to_datetime(start).tz_localize("UTC")
    end = start + pandas.Timedelta(hours=23, minutes=30) 
    combined_data["x"] = pandas.to_datetime(combined_data["x"]) 
    combined_data = combined_data[(combined_data["x"] >= start) & (combined_data["x"] < end)]
    if "y1" in combined_data.columns and "y2" in combined_data.columns:
        combined_data["y"] = combined_data["y2"] - combined_data["y1"]
        combined_data.loc[combined_data["y"] < 0, "y"] = 0
    else:
        combined_data["y"] = 0
    combined_data = combined_data.drop(columns=["y1", "y2"])
    combined_json = combined_data.to_json(orient="records", date_format="iso")
    return combined_json



@app.route("/<hash_id>/simulate_excess_energy")
def simulate_excess_energy(hash_id):
    return flask.render_template("simulate_excess_energy.html", hash_id=hash_id)

@app.route("/<hash_id>/process", methods=["GET", "POST"])
def process_esb(hash_id):
    if flask.request.method == "POST":

        # Check if the post request has the file part
        if "esb_file" not in flask.request.files:
            # back to details
            return flask.redirect(flask.url_for("get_detailed_user_data", hash_id=hash_id))
        file = flask.request.files["esb_file"]
        # If the user does not select a file, the browser submits an empty file without a filename
        if file.filename == "":
            # Invalid back to details
            return flask.redirect(flask.url_for("get_detailed_user_data", hash_id=hash_id))
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)


            file.save(os.path.join(app.config["UPLOAD_FOLDER"], hash_id + ".csv"))
            return flask.redirect(flask.url_for("get_detailed_user_data", hash_id=hash_id))
    # No valid file redirect
    return flask.redirect(flask.url_for("get_detailed_user_data", hash_id=hash_id))


ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"

def encode_id(n: int) -> str:
    #salt, hash it, base36 it
    hash_encoder = hashids.Hashids(salt="this is my salt", min_length=8, alphabet=ALPHABET)
    hashid = hash_encoder.encode(n)
    return hashid

def decode_id(hash_id: str) -> int:
    hash_encoder = hashids.Hashids(salt="this is my salt", min_length=8, alphabet=ALPHABET)
    n = hash_encoder.decode(hash_id)[0]
    return n


def get_lat_lon_from_eircode(eircode):
    base_url = "https://nominatim.openstreetmap.org/search"
    headers = {"User-Agent": "MickSolarCalc/1.0"} 
    params = {"q": eircode, "format": "json"}
    response = requests.get(base_url, headers=headers, params=params)
    data = response.json()

    if data:
        return float(data[0]["lat"]), float(data[0]["lon"])
    else:
        return None
    
# Function that calculates power output of a solar panel 
# Makes assumptions about weather
# date and time not constant for this project
def calc_power_output(latitude, longitude, rated_power_per_panel, number_of_panels, panel_tilt, panel_azimuth):    
    location = pvlib.location.Location(latitude, longitude)
    #clearsky_irradiance =  location.get_clearsky(times)

    # Typical meteorogical year using the pvgis api
    data, _, _, _ = pvlib.iotools.get_pvgis_tmy(latitude=latitude, longitude=longitude)

    solar_position = location.get_solarposition(data.index)

    #solar_position = location.get_solarposition(date_time)
    # Optimisic estimate of solar irradiation because it assumes clear skies
    # clear_sky = location.get_clearsky(date_time, model="ineichen")

    # Calculates the irradiance watts per m squared of the panel surface
    # Pvlib assumes a gorund albedo of around .2 which is correspondant with grass and soil, may differ for highly reflective surfaces like snow
    irradiance = pvlib.irradiance.get_total_irradiance(
        surface_tilt=panel_tilt,
        surface_azimuth=panel_azimuth,
        solar_zenith=solar_position['zenith'],
        solar_azimuth=solar_position['azimuth'],
        ghi=data['ghi'],
        dni=data['dni'],
        dhi=data['dhi'])
    
    #default values entered for wind_speed, u0, and u1
    module_temperature = pvlib.temperature.faiman(poa_global=irradiance["poa_global"], temp_air=data["temp_air"], wind_speed=1, u0=25.0, u1=6.84)

    dc_power = pvlib.pvsystem.pvwatts_dc(
    g_poa_effective=irradiance['poa_global'],
    # simplified assumptions that module and cell temperature are the same
    temp_cell=module_temperature,
    pdc0=rated_power_per_panel,
    gamma_pdc=-0.004,
    temp_ref=25.0,
    )
    #dc_power.index = dc_power.index.replace(year=2024)
    dc_power.index = dc_power.index.map(lambda x: x.replace(year=2024))

    # Plot x-axis: time, y-axis: dc_power on a line chart thingy
    #matplotlib.pyplot.plot
    

    return dc_power * number_of_panels

def get_avg_value(values):
    values = numpy.array(values)
    for i in range(1, len(values)):
        if numpy.isnan(values[i]):
            values[i] = (values[i-1] + values[i+1]) / 2
    return values
'''
# This function was misinformed in its creation going to keep it in until i figure out if its completely misguided or not
def rounds_and_calculates_a_year_of_dates():
    tz = "Europe/Dublin"
    #times = pd.date_range(start="2025-03-29 00:00:00", end="2025-03-30 00:00:00", freq="30min", tz="Europe/Dublin")
    tz_formatted = pytz.timezone(tz)
    time_now_aware = datetime.now(tz_formatted)
    delta = timedelta(minutes=30)
    time_rounded_down = time_now_aware - (time_now_aware - datetime.min.replace(tzinfo=timezone.utc)) % delta
    return pandas.date_range(start=time_rounded_down.replace(year=(int(time_rounded_down.year) - 1)), end=time_rounded_down, freq="30min", tz=tz)'
'''