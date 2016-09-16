import enum

from distutils.version import LooseVersion


class ImageKind(enum.Enum):
    CENT7 = 'CENT7'


def find_cent7_image(images):
    """Tries to find the centos7 images given a cloud instance."""
    possible_images = []
    for image in images:
        try:
            if image['group'] != 'CentOS 7' or \
               image.get('protected') or image['status'] != 'active':
                continue
            if image['os.spec'].startswith("CentOS Linux release 7") \
               and image['os.family'] == 'linux' \
               and image['name'].startswith("centos7-base"):
                possible_images.append(image)
        except KeyError:
            pass
    if not possible_images:
        return None
    else:
        image_by_names = {img.name: img for img in possible_images}
        image_by_names_ver = sorted(
            # This will do good enough, until we can figure out what the
            # actual naming scheme is here...
            [LooseVersion(img.name) for img in possible_images], reverse=True)
        return image_by_names[str(image_by_names_ver[0])]


def find_image(cloud, kind):
    """Tries to find some images (of a given kind) given a cloud instance."""
    if kind == ImageKind.CENT7:
        return find_cent7_image(cloud.list_images())
    else:
        raise NotImplementedError("Unsupported image kind: %s" % (kind))
