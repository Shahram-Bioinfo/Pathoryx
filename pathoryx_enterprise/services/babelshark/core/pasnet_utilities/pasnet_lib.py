import sys
import pyodbc
import pandas as pd

############# IMPORTANT – PLEASE READ ############################
# Operational and security guidelines for querying the Nexus LIS database:
# - The Nexus system is a production, high-priority clinical system. Queries must be executed carefully to avoid impacting routine operations.
# - NEVER share the server address, username, or password with anyone. NEVER hard-code credentials, especially in code that is stored in a git repository.
# - Credentials should be supplied via environment variables or command-line arguments (as demonstrated in this example), not embedded in source code.
# - This script MUST NOT perform any write operations; it is strictly read-only and intended only to retrieve information.
# - Keep the number of database requests as low as reasonably possible. This implementation batches multiple cases
#   (case_id_list) into a single database request, but you may also provide a single case ID when needed.
# - Avoid issuing too many requests in a short period of time; we must not overload the Nexus LIS system, as it is a critical routine system.


# Column names available in the view vw_UniHeidelberg_TC_Anbindung:
vw_UniHeidelberg_TC_Anbindung_column_names = ('Mandant Eingangsnummer Eingangsdatum Materialarten Entnahmedatum EinsenderKuerzel '
                        'EinsenderName1 EinsenderName2 EinsenderName3 EinsenderName4 EinsenderName5 EinsenderName6 '
                        'EinsenderStrasse EinsenderHausnr EinsenderPLZ EinsenderOrt EinsenderLand Krankenhaus '
                        'KhsStrasse KhsHausnr KhsPLZ KhsOrt KhsLand KhsEinsenderstation PatientVorname '
                        'PatientNachname PatientGeburtsname PatientGeburtsdatum PatientPID PatientAufnahmenr '
                        'PatientGeschlecht Befundtext Diagnoseschluessel Befundempfaenger').split(' ')

class PasnetInfoRetriever:
    def __init__(self, server, username, password):
        self.db_connection = pyodbc.connect("Driver={SQL Server};Server=" + server + ";uid=" + username + ";Pwd=" + password)
        self.cursor = self.db_connection.cursor()
        print("Connection established successfully.")

    def close(self):
        if self.db_connection:
            try:
                self.db_connection.close()
            finally:
                self.db_connection = None

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.db_connection.rollback()
        else:
            pass
        self.close()

    def __del__(self):
        self.close()

    def get_slide_infos_from_case(self, case_id):
        '''
        Retrieve slide-level information (for example stain and slide_id) for a given case.

        :param case_id: Case identifier in the format 'E/2026/123456'.
        :return: A pandas DataFrame with slide information, or None if no matching slides are found.
        '''

        if not "/" in case_id:
            raise ValueError("Wrong case id format. Please use the case-id format with dashes, e.g. E/2026/123456")

        case_guid_query = f"SELECT GUID FROM dbo.MedOrder WHERE OrderNumber = '{case_id}'"

        df = pd.read_sql(case_guid_query, self.db_connection)

        if len(df['GUID']) == 0:
            return None
        elif len(df['GUID']) > 1:
            raise Exception(f"There are more than one GUID for {case_id} in table dbo.MedOrder!? Should be unique!?")

        med_order_guid = df['GUID'][0]

        # slide_info_query = f"SELECT label as slide_id, Name as staining, LabProtocolName FROM dbo.Container WHERE MedOrder = '{med_order_guid}'"
        slide_info_query = f"SELECT label as slide_id, Name as staining FROM dbo.Container WHERE MedOrder = '{med_order_guid}'"

        df = pd.read_sql(slide_info_query, self.db_connection)

        # sort out rows where slide_id is None
        df = df[df['slide_id'].notna()]

        # sort out rows where we actually dont have a slide (e.g. E2026123456SA-1 is not a slide, it is a block!)
        df = df[df['slide_id'].str.len() >= 17]

        return df

    def does_slide_id_exist(self, slide_id):
        '''
        Check whether the given slide_id exists in the PASNET database.

        :param slide_id: The slide_id to check.
        :return: True if the slide_id exists, otherwise False.
        '''

        self.cursor.execute(f"SELECT label FROM dbo.Container WHERE label = '{slide_id}'")

        if self.cursor.fetchall():
            return True
        return False

    def get_TC_infos_from_case_list(self, case_id_list:list, metadata_to_collect=['PatientVorname', 'PatientNachname', 'PatientPID', 'PatientAufnahmenr']):
        '''
        Collect case-level metadata from the LIS database via SQL.

        :param case_id_list: List of case IDs from which metadata should be collected.
        :param metadata_to_collect: List of metadata field names to retrieve; defaults to
                                   ['PatientVorname', 'PatientNachname', 'PatientPID', 'PatientAufnahmenr'].
        :return: Dictionary of collected metadata from the LIS database with structure:
                 {case_id: {attribute1: value1, attribute2: value2, ...}, ...}
        '''

        # preprocess case-id patterns:
        case_id_list = [case if "/" in case else case[0] + '/' + case[1:5] + '/' + case[5:] for case in case_id_list]
        case_id_list = [case[:11+2] for case in case_id_list] # cut away any "S" at the end of case id


        # --- Build SQL query ---
        # Convert Python list into SQL-friendly IN clause (with quoted strings)
        case_ids_sql = "(" + ", ".join([f"'{case}'" for case in case_id_list]) + ")"
        metadata_to_collect = ['Eingangsnummer'] + metadata_to_collect
        sql_query = "SELECT " + ", ".join(metadata_to_collect) + f" FROM vw_UniHeidelberg_TC_Anbindung WHERE Eingangsnummer IN {case_ids_sql}"

        for metadata_key in metadata_to_collect:
            if metadata_key not in vw_UniHeidelberg_TC_Anbindung_column_names:
                raise ValueError(f"value {metadata_key} not allowed in metadata_to_collect list. Allowed values: {vw_UniHeidelberg_TC_Anbindung_column_names}")

        # prepare dictionary to store results in, which has shape {case_id: {metadata_key: metadata_value, ...}, ...}
        cases_metadata = {k: {} for k in case_id_list}

        try:
            self.cursor.execute(sql_query)
            rows = self.cursor.fetchall()

            if not rows:
                return None
            else:
                for row in rows:
                    case_data = {}
                    case_id = None
                    for idx, col in enumerate(metadata_to_collect):
                        if col == 'Eingangsnummer' and row[idx] is None:
                            #print("Error: 'Eingangsnummer' is NULL, cannot identify case.")
                            break
                        elif col == 'Eingangsnummer':
                            case_id = row[idx]
                            #print(f"Processing case: {case_id}")
                            continue

                        value = row[idx]
                        if value is None:
                            #print(f"Warning: Field '{col}' is NULL for case {case_id}.")
                            pass
                        case_data[col] = value

                    if case_id:
                        cases_metadata[case_id] = case_data

            return cases_metadata

        except Exception as e:
            raise e

        finally:
            pass
