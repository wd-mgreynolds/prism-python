import json
import logging
import sys
import click

from prism import schema_compact, load_schema, upload_file, truncate_table

logger = logging.getLogger("prismCLI")


@click.command("get")
@click.option(
    "-n",
    "--isName",
    is_flag=True,
    default=False,
    help="Flag to treat the table argument as a name.",
)
@click.option(
    "-l",
    "--limit",
    type=int,
    default=None,
    help="The maximum number of object data entries included in the response, default=all.",
)
@click.option(
    "-o",
    "--offset",
    type=int,
    default=None,
    help="The offset to the first object in a collection to include in the response.",
)
@click.option(
    "-t",
    "--type",
    "type_",
    default="summary",
    type=click.Choice(["summary", "full", "permissions"], case_sensitive=False),
    help="How much information returned for each table.",
)
@click.option(
    "-c",
    "--compact",
    is_flag=True,
    default=False,
    help="Compact the table schema for use in edit (put) operations.",
)
@click.option(
    "-s",
    "--search",
    is_flag=True,
    help="Enable substring search of NAME in api name or display name.",
)
@click.argument("table", required=False)
@click.pass_context
def tables_get(ctx, isname, table, limit, offset, type_, compact, search):
    """List the tables or datasets permitted by the security profile of the current user.

    [TABLE] Prism table ID or name (--isName flag) to list.
    """

    p = ctx.obj["p"]

    # Query the tenant...see if the caller said to treat the
    # table as a name, AND that a table was provided.
    if not isname and table is not None:
        # When using an ID, the GET:/tables operation returns a simple
        # dictionary of the table definition.
        table = p.tables_get(table_id=table, type_=type_)

        if table is None:
            logger.error(f"Table ID {table} not found.")
            sys.exit(1)

        if compact:
            table = schema_compact(table)

        logger.info(json.dumps(table, indent=2))
    else:
        # When querying by name, the get operation returns a
        # dict with a count of found tables and a list of
        # tables.
        tables = p.tables_get(table_name=table, limit=limit, offset=offset, type_=type_, search=search)

        if tables["total"] == 0:
            logger.error(f"Table ID {table} not found.")
            return

        if compact:
            for tab in tables["data"]:
                tab = schema_compact(tab)

        logger.info(json.dumps(tables, indent=2))


@click.command("create")
@click.option("-n", "--table_name", help="Table name - overrides name from schema.")
@click.option("-d", "--displayName", help="Specify a display name - defaults to name.")
@click.option(
    "-e",
    "--enableForAnalysis",
    type=bool,
    is_flag=True,
    default=None,
    help="Enable this table for analytics.",
)
@click.option("-s", "--sourceName", help="The API name of an existing table to copy.")
@click.option("-w", "--sourceWID", help="The WID of an existing table to copy.")
@click.argument("file", required=False, type=click.Path(exists=True))
@click.pass_context
def tables_create(ctx, table_name, displayname, enableforanalysis, sourcename, sourcewid, file):
    """
    Create a new table with the specified name.

    [FILE] Optional file containing a Prism schema definition for the new table.

    Note: A schema file, --sourceName, or --sourceWID must be specified.
    """
    p = ctx.obj["p"]

    # We can assume a schema was found/built - get_schema sys.exits if there is a problem.
    schema = load_schema(p, file, sourcename, sourcewid)

    # Initialize a new schema with the particulars for this table operation.
    if table_name is not None:
        # If we got a name, set it in the table schema
        schema["name"] = table_name.replace(" ", "_")  # Minor clean-up

        # Force the display name - there cannot be duplicate displayNames
        # in the data catalog.
        schema["displayName"] = table_name

        logger.debug(f'setting table name to {schema["name"]}')
    elif "name" not in schema:
        # The schema doesn't have a name and none was given - exit.
        # Note: this could be true if we have a schema of only fields.
        logger.error("Table --table_name must be specified.")
        sys.exit(1)

    if displayname is not None:
        # If we got a display name, set it in the schema
        schema["displayName"] = displayname
    elif "displayName" not in schema:
        # Default the display name to the name if not in the schema.
        schema["displayName"] = table_name
        logger.debug(f'defaulting displayName to {schema["displayName"]}')

    if enableforanalysis is not None:
        schema["enableForAnalysis"] = enableforanalysis
    elif "enableForAnalysis" not in schema:
        # Default to False - do not enable.
        schema["enableForAnalysis"] = False
        logger.debug("defaulting enableForAnalysis to False.")

    # Create the table.
    table_def = p.tables_post(schema)

    if table_def is not None:
        logger.info(json.dumps(table_def, indent=2))
    else:
        logger.error(f'Error creating table {schema["name"]}.')
        sys.exit(1)


@click.command("edit")
@click.option("-i", "--table_id", help="Table id - overrides name from schema.")
@click.option("-n", "--table_name", help="Table name - overrides name from schema.")
@click.option(
    "-t",
    "--truncate",
    is_flag=True,
    default=False,
    help="Truncate the table before updating.",
)
@click.argument("file", required=True, type=click.Path(exists=True, dir_okay=False, readable=True))
@click.pass_context
def tables_edit(ctx, file, table_id, table_name, truncate):
    """Edit the schema for an existing table.

    [FILE] File containing an updated schema definition for the table.
    """
    p = ctx.obj["p"]

    # The user must specify a file containing a Prism schema,
    # perhaps from a tables GET operation or a CSV of column
    # definitions.
    schema = load_schema(file=file)

    if schema is None:
        logger.error("Invalid schema for edit operation - invalid file.")
        sys.exit(1)

    # If the user passed an ID or name then override the
    # definition that may exist in the schema file.
    if table_id is not None or table_name is not None:
        table = p.tables_get(table_id=table_id, table_name=table_name, type="full")

        if table is None:
            logger.error("Table for ID or name not found.")
            sys.exit(1)

        # Based on the table definition, override (or set) the ID
        # into the schema loaded from the file.
        schema["id"] = table["id"]

    # As a last sanity check, make sure we have the minimum schema
    # definition to perform an edit operation.,
    if "id" not in schema or "fields" not in schema:
        logger.error("Schema does not contain ID or fields values for edit operation.")
        sys.exit(1)

    # We have the completed schema, see if the user wants to
    # truncate existing data.  NOTE: schema changes cannot be
    # applied if rows exist in the table.
    if truncate:
        # If the table cannot be truncated, error messages have
        # already been generated, simply exit.
        if truncate_table(p=p, table_id=schema["id"]) is None:
            sys.exit(1)

    table = p.tables_put(schema)

    if table is None:
        logger.error("Error updating table.")
    else:
        logger.info(json.dumps(table, indent=2))


@click.command("patch")
@click.option(
    "-n",
    "--isName",
    is_flag=True,
    default=False,
    help="Flag to treat the table argument as a name.",
)
@click.option(
    "--displayName",
    is_flag=False,
    flag_value="*-clear-*",
    default=None,
    help="Set the display name for an existing table.",
)
@click.option(
    "--description",
    is_flag=False,
    flag_value="*-clear-*",
    default=None,
    help="Set the display name for an existing table.",
)
@click.option(
    "--documentation",
    is_flag=False,
    flag_value="*-clear-*",
    default=None,
    help="Set the documentation for an existing table.",
)
@click.option(
    "--enableForAnalysis",
    is_flag=False,
    default=None,
    type=click.Choice(["true", "false"], case_sensitive=False),
)
@click.argument("table", required=True, type=str)
@click.argument("file", required=False, type=click.Path(dir_okay=False))
@click.pass_context
def tables_patch(ctx, isname, table, file, displayname, description, documentation, enableforanalysis):
    """Edit the specified attributes of an existing table with the specified id (or name).

    If an attribute is not provided in the request, it will not be changed.  To set an
    attribute to blank (empty), include the attribute without specifying a value.

    TABLE The ID or API name (use -n option) of the table to patch.

    [FILE] Optional file containing patch values for the table.
    """

    p = ctx.obj["p"]

    # Figure out the new schema either by file or other table.
    patch_data = {}

    # If a file is specified, there can only be patch values and
    # cannot be a full Prism schema.
    if file is not None:
        try:
            with open(file, "r") as patch_file:
                patch_data = json.load(patch_file)
        except Exception as e:
            logger.error(e)
            sys.exit(1)

        if not isinstance(patch_data, dict):
            logger.error("invalid patch file - should be a dictionary")
            sys.exit(1)

        valid_attributes = [
            "displayName",
            "description",
            "enableForAnalysis",
            "documentation",
        ]

        for patch_attr in patch_data.keys():
            if patch_attr not in valid_attributes:
                logger.error(f'unexpected attribute "{patch_attr}" in patch file')
                sys.exit(1)

    def set_patch_value(attr, value):
        """Utility function to set or clear a table attribute.

        If the user specifies an attribute but does not provide a value,
        add a patch value to clears/null the value
        """
        if value == "*-clear-*":
            patch_data[attr] = ""
        else:
            patch_data[attr] = value

    # See if the user creating new patch variables or overriding
    # values from the patch file.

    # Note: specifying the option without a value creates a
    # patch value to clear the value in the table def.  The
    # caller can override the values from the patch file using
    # command line arguments.
    if displayname is not None:  # Specified on CLI
        set_patch_value("displayName", displayname)

    if description is not None:
        set_patch_value("description", description)

    if documentation is not None:
        set_patch_value("documentation", documentation)

    if enableforanalysis is not None:
        if enableforanalysis.lower() == "true":
            patch_data["enableForAnalysis"] = "true"
        else:
            patch_data["enableForAnalysis"] = "false"

    # The caller must be asking for something to change!
    if len(patch_data) == 0:
        logger.error("Specify at least one table schema value to update.")
        sys.exit(1)

    # Identify the existing table we are about to patch.
    if isname:
        # Before doing anything, table name must exist.
        tables = p.tables_get(table_name=table)  # Exact match

        if tables["total"] == 0:
            logger.error(f'Table name "{table}" not found.')
            sys.exit(1)

        resolved_id = tables["data"][0]["id"]
    else:
        # No verification needed, simply assume the ID is valid.
        resolved_id = table

    table = p.tables_patch(table_id=resolved_id, patch=patch_data)

    if table is None:
        logger.error(f"Error updating table ID {resolved_id}")
    else:
        logger.info(json.dumps(table, indent=2))


@click.command("upload")
@click.option(
    "-n",
    "--isName",
    is_flag=True,
    default=False,
    help="Flag to treat the table argument as a name.",
)
@click.option(
    "-o",
    "--operation",
    default="TruncateAndInsert",
    help="Operation for the table operation - default to TruncateAndInsert.",
)
@click.argument("table", required=True)
@click.argument("file", nargs=-1, type=click.Path(exists=True))
@click.pass_context
def tables_upload(ctx, table, isname, operation, file):
    """
    Upload a file into the table using a bucket.

    [TABLE] A Prism Table identifier.
    [FILE] One or more CSV or GZIP.CSV files.
    """

    p = ctx.obj["p"]

    if len(file) == 0:
        logger.error("No files to upload.")
        sys.exit(1)

    if isname:
        results = upload_file(p, table_name=table, file=file, operation=operation)
    else:
        results = upload_file(p, table_id=table, file=file, operation=operation)

    logger.debug(json.dumps(results, indent=2))


@click.command("truncate")
@click.option(
    "-n",
    "--isName",
    is_flag=True,
    default=False,
    help="Flag to treat the table argument as a name.",
)
@click.argument("table", required=True)
@click.pass_context
def tables_truncate(ctx, table, isname):
    """
    Truncate the named table.

    [TABLE] The Prism Table ID or API name of the table to truncate.
    """
    p = ctx.obj["p"]

    if isname:
        result = truncate_table(p, table_name=table)
    else:
        result = truncate_table(p, table_id=table)

    if result is None:
        logger.warning("Table was not truncated.")
    else:
        logger.info(json.dumps(result, indent=2))
