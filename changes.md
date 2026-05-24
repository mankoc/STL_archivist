Let´s merge the crawl and update crawler functionality:

Instead of a single folder to crawl add the list of already crawled folders like on update folders, with options for adding and deleting folders.

Crawling logic should be similar to update crawled folders:
* Do the crawling in two steps:
1 - First scan all files and hashes for all the folders identifying new files, deleted files and modified files using the existing file hashes.
2 - Locate possible moved files: files that have the exact same hash but it´s located into a different folder.  If some file is found as moved copy the metadata and images from the deleted archive into the new archive, mark the new file as moved and remove the "deleted" file.
3 - Do the metadata extraction on the new and updated files if enabled.


The log section of crawler should show a table with columns filename, path of the file, changes (either new, deleted, updated, etc) like on update crawled folders

Finally disable the update crawler folders menu item and button

Rename "Open crawler" to "Crawler"