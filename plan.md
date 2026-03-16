Let´s create a desktop app.   I want to create a program that creates a browsable database of stl models stored in a folder in a hard drive.

# Definitions
*  A "model" is a collection of stl files plus supported 3d model files such as .lys or .chitu that can be assembled to a single figure.

We have a single folder such as /mnt/f/STL that contains a series of subfolders that organize the files in categories and authors.
The files will be archive files, mainly 7z files, occassionally zip and rar files.
Inside each archive there will be a series of stl files that contains a single model (or in some cases multiple models), images files and slicer files such as .lys.  In some occasions there will be an .url file pointing to the source web and an info.json file containing metadata information such as:
{
    "name": "-Cyborg Hulk-",
    "author": "Caleb Nefzen",
    "distributor": "Artstation",
    "url": "https://calebnefzen.artstation.com/projects/yJ6ZKK",
    "description": "I was hesitant at first...  Hulk.. for me is a bit boring to sculpt.. cose  there are already \nso many great hulks out there. But my client showed me a comic with this Cyborg Hulk. \nso ...i couldn\u2019t say no , since  Im a Big fan of Terminator.\n",
    "tags": [
        "hulk",
        "cyborg",
        "statue",
        "collectible",
        "comic",
        "geek",
        "marvel hulk"
    ]
} 

We want to create a single gui application that in different tabs have:

1-  A crawler that 
    * traverses a source file, 
    * looks into each of the archives 
    * retrieves file information inside the archives, 
    * extracts thumbnails of the images there, 
    * infers metadata from the paths and info files.
    * stores the data retrieved into a database.
    The crawler must update the existing information if the data for a file is already extracted (use the file hash to check if a model is updated from the one available)
2-   A browser of the information in the database allowing for search fields by author, tags, names etc.
This browser must be able to edit the different fields in the database adding and modifying information as needed.
3-  Model data editor: single page that can be called from the right button menu on the browser that shows all the editable parameters in the model.  Should have features for extracting all the fields from the model file path and content.
Base the table view and editor on what´s available on calibre for ebooks.
3-  Tag editor:  allows to mass edit some tags, merging similar names and adding/deleting tag categories


# Database
Initially the database will be sqlite but plan for migrating it into a more powerful database in the future.
The database for a model must contain the following fields at least:
* Title: name of the model
* Author: Name of the producer of the model.
* Type:  If it´s from patreon, a kikcstarter, myminifactory tribes of where it was available.  This can be unkown in any case.
* Date:  If a date is available on the file path or name use this as release date
* URL:  Url to the source.
* Tags: list of tags associated with this model such as "hulk", "cyborg", statue etc these tags should be common to all models (reused from a list of tags table)
* Images:  Extracted images from the archive files.

# Crawler
The crawler must extract and infer the fields for the database fields using the path, name and contents of each archive.  Look inside info.json if available to obtain extra metadata information

# Technologies
Use pyqt for the graphical interface, with a modern skin. For database use sqlite, but leave everything prepared to move into another database if required.
Try to use memory efficient methods to do the crawling and database access to avoid runoff memory requirements.
The code should be easily deployed on linux and windows machines.

# Additional tasks
* Generate the requirements.txt, gitignore and other files required by the project if not available