"""
CKAN DOI Plugin
"""
from pylons import config
from datetime import datetime
from logging import getLogger
import ckan.plugins as p
import ckan.logic as logic
from ckan.lib import helpers as h
from ckan import model
from ckanext.doi.model import doi as doi_model
from ckanext.doi.lib import get_doi, publish_doi, update_doi, create_unique_identifier, get_site_url, build_metadata, validate_metadata, record_existing_unique_identifier
from ckanext.doi.helpers import package_get_year, now, get_site_title

get_action = logic.get_action

log = getLogger(__name__)


class DOIPlugin(p.SingletonPlugin, p.toolkit.DefaultDatasetForm):
    """
    CKAN DOI Extension
    """
    p.implements(p.IConfigurable)
    p.implements(p.IConfigurer)
    p.implements(p.IPackageController, inherit=True)
    p.implements(p.ITemplateHelpers, inherit=True)

    # IConfigurable
    def configure(self, config):
        """
        Called at the end of CKAN setup.
        Create DOI table
        """
        if model.package_table.exists():
            doi_model.doi_table.create(checkfirst=True)

    # IConfigurer
    def update_config(self, config):
        # Add templates
        p.toolkit.add_template_directory(config, 'theme/templates')

    # IPackageController
    def after_create(self, context, pkg_dict):
        """
        A new dataset has been created, so we need to create a new DOI
        NB: This is called after creation of a dataset, and before resources have been added so state = draft
        @param context:
        @param pkg_dict:
        @return:
        """
        # Only create a new DOI if the user has requested it
        if hasattr(pkg_dict, "doi_auto_create"):
            # Create a new doi
            create_unique_identifier(pkg_dict['id'])

        return pkg_dict

    # IPackageController

    def after_update(self, context, pkg_dict):
        """
        Dataset has been created / updated
        Check status of the dataset to determine if we should publish DOI to datacite network

        @param pkg_dict:
        @return: pkg_dict
        """

        package_id = pkg_dict['id']

        # Load the original package, so we can determine if user has changed any fields
        orig_pkg_dict = get_action('package_show')(context, {
            'id': package_id
        })

        # Metadata created isn't populated in pkg_dict - so copy from the original
        pkg_dict['metadata_created'] = orig_pkg_dict['metadata_created']

        # Load the local DOI
        doi = get_doi(package_id)

        if 'doi_auto_create' in pkg_dict:
            if not doi:
                # Overwrite any existing identifier with a newly minted DOI
                create_unique_identifier(package_id)
            pkg_dict['identifier'] = doi.identifier
            pkg_dict['identifier_type'] = 'doi'
            # Remove the auto create field from the dataset pkg
            pkg_dict.pop('doi_auto_create')

        # If we don't have a DOI, create one
        # This could happen if the DOI module is enabled after a dataset has been created
        if not doi:
            if pkg_dict['identifier'] and pkg_dict['identifier_type'] == 'doi':
                # We don't have a DOI, we're not auto creating one, the user has input an existing doi
                new_doi = record_existing_unique_identifier(
                    package_id, pkg_dict['identifier'])

                # If the existing doi was successfully recorded
                if new_doi:
                    # Assign the doi
                    doi = new_doi
            else:
                # TODO: Remove DOI?
                return pkg_dict
        elif pkg_dict['identifier'] and pkg_dict['identifier_type'] == 'doi':
            # A DOI has been provided by the user
            # Check if the DOI in our table has the same identifier
            if doi.identifier is not pkg_dict['identifier']:
                # Remove from our DOI table
                doi.delete()

                # Make sure the new DOI is registered with DataCite
                new_doi = record_existing_unique_identifier(
                    package_id, pkg_dict['identifier'])
                if new_doi:
                    doi = new_doi

        elif pkg_dict['identifier_type'] == 'other':
            # Remove the DOi from our database
            doi.delete()

            # Let the other type identifier persist in the dataset package
            return pkg_dict

        # Is this active and public? If so we need to make sure we have an active DOI
        if pkg_dict.get('state', 'active') == 'active' and not pkg_dict.get('private', False):

            # Build the metadata dict to pass to DataCite service
            metadata_dict = build_metadata(pkg_dict, doi)

            # Perform some basic checks against the data - we require at the very least
            # title and author fields - they're mandatory in the DataCite Schema
            # This will only be an issue if another plugin has removed a mandatory field
            validate_metadata(metadata_dict)

            # Is this an existing DOI? Update it
            if doi.published:

                # Before updating, check if any of the metadata has been changed - otherwise
                # We end up sending loads of revisions to DataCite for minor edits
                # Load the current version
                orig_metadata_dict = build_metadata(orig_pkg_dict, doi)
                # Check if the two dictionaries are the same
                if cmp(orig_metadata_dict, metadata_dict) != 0:
                    # Not the same, so we want to update the metadata
                    update_doi(package_id, **metadata_dict)
                    h.flash_success('DataCite DOI metadata updated')

                    # TODO: If editing a dataset older than 5 days, create DOI revision

            # New DOI - publish to datacite
            else:
                h.flash_success('DataCite DOI created')
                publish_doi(package_id, **metadata_dict)

        return pkg_dict

    # IPackageController
    def after_show(self, context, pkg_dict):
        # Load the DOI ready to display
        doi = get_doi(pkg_dict['id'])
        if doi:
            pkg_dict['doi'] = doi.identifier
            pkg_dict['doi_status'] = True if doi.published else False
            pkg_dict['domain'] = get_site_url().replace('http://', '')
            pkg_dict['doi_date_published'] = datetime.strftime(
                doi.published, "%Y-%m-%d") if doi.published else None
            pkg_dict['doi_publisher'] = config.get("ckanext.doi.publisher")

    # ITemplateHelpers
    def get_helpers(self):
        return {
            'package_get_year': package_get_year,
            'now': now,
            'get_site_title': get_site_title
        }
