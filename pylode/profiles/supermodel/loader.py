import logging
import time

import httpx
from rdflib import Graph, Dataset, URIRef, OWL, PROF, RDF, DCTERMS

from pylode.profiles.supermodel.namespace import LODE

# TODO: Set up logging elsewhere
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

httpcore_logger = logging.getLogger('httpcore')
httpcore_logger.setLevel(logging.WARNING)

PYLODE_CONFIG_GRAPH = "urn:graph:pylode-config"


MEDIA_TYPES = {
    "text/turtle": "text/turtle",
    "application/n-triples": "application/n-triples",
    "application/n-quads": "application/n-quads",
}


def fetch(url: str, client: httpx.Client, content_type: str = "text/turtle") -> tuple[str, str]:
    # TODO: Remove proxies
    proxies = {
        "https://linked.data.gov.au/def/csdm/geometryprim": "http://localhost:8000/geometryprim.ttl",
        "http://www.opengis.net/ont/geosparql": "https://cdn.jsdelivr.net/gh/opengeospatial/ogc-geosparql@master/1.1/geo.ttl",
        "http://datashapes.org/dash": "https://cdn.jsdelivr.net/gh/zazuko/rdf-vocabularies@master/ontologies/dash/dash.nq",
    }
    if url in proxies:
        logger.debug(f"Using proxy URL {url} to {proxies[url]}")
        url = proxies[url]
    response = client.get(url, headers={"Accept": content_type})
    if response.status_code != 200:
        raise httpx.HTTPError(f"HTTP {response.status_code}: Failed to fetch remote resource from {url}. {response.text}")

    content_type_headers = response.headers.get("Content-Type")
    for media_type in MEDIA_TYPES:
        if media_type in content_type_headers:
            content_type = media_type
    return response.text, content_type


class ProfilesDataset(Dataset):
    def load_owl_imports(self, graph: Graph):
        """Load all owl:imports values recursively."""
        import_values = [str(v) for v in graph.objects(None, OWL.imports) if str(v) not in self.external_resources]
        if import_values:
            for remote_resource in import_values:
                if str(remote_resource) not in self.external_resources:
                    logger.debug(f"Fetching remote resource {remote_resource} from owl:imports.")
                    _graph = Graph()
                    try:
                        data, content_type = fetch(str(remote_resource), self.client)
                        _graph.parse(data=data, format=content_type)
                        # _graph.parse(str(remote_resource))
                        self.external_resources.add(str(remote_resource))
                        return self.load_owl_imports(_graph)
                    except Exception as err:
                        raise RuntimeError(f"Failed to parse data from {remote_resource}. {err}")

        return graph

    def load_profiles(self, graph: Graph, prev_graph: Graph):
        profiles = graph.subjects(RDF.type, PROF.Profile)
        for profile in profiles:
            cbd = graph.cbd(profile)
            _graph = Graph(identifier=str(profile))
            _graph.__iadd__(cbd)

            other = graph - cbd
            other = self.load_owl_imports(other)
            prev_graph.__iadd__(other)
            self.add_graph(_graph)
            graph = self.load_owl_imports(graph)

            prof_resource_descriptors = graph.objects(None, PROF.hasResource)
            for prof_resource_descriptor in prof_resource_descriptors:
                mediatype = graph.value(prof_resource_descriptor, DCTERMS.format)
                role = graph.value(prof_resource_descriptor, PROF.hasRole)
                remote_resource = graph.value(prof_resource_descriptor, PROF.hasArtifact)

                if role == LODE.config:
                    config_graph = self.get_graph(URIRef(PYLODE_CONFIG_GRAPH))
                    logger.debug(f"Fetching remote resource {remote_resource} from PROF resource descriptor. A pylode config.")
                    data, content_type = fetch(str(remote_resource), self.client, str(mediatype))
                    config_graph.parse(data=data, format=content_type)
                elif str(mediatype) in MEDIA_TYPES:
                    logger.debug(f"Fetching remote resource {remote_resource} from PROF resource descriptor.")
                    new_graph = Graph()
                    data, content_type = fetch(str(remote_resource), self.client, str(mediatype))
                    new_graph.parse(data=data, format=content_type)
                    # new_graph.parse(str(remote_resource))
                    graph.__iadd__(new_graph)
                    self.load_profiles(new_graph, graph)

    def __init__(self, root_profile_iri: str, data: str):
        super().__init__(default_union=True)
        self.root_profile_iri = root_profile_iri
        self.client = httpx.Client(follow_redirects=True)

        # Tracks what we have loaded so far.
        self.external_resources = set()

        graph = Graph()
        graph.parse(data=data)
        graph = self.load_owl_imports(graph)
        initial_profiles_graph = Graph()
        for profile in graph.subjects(RDF.type, PROF.Profile):
            cbd = graph.cbd(profile)
            initial_profiles_graph.__iadd__(cbd)
            graph = graph - cbd

        self.add_graph(Graph(identifier=PYLODE_CONFIG_GRAPH))
        self.load_profiles(initial_profiles_graph, graph)

        root_graph = Graph(identifier=root_profile_iri)
        root_graph.__iadd__(graph)
        self.add_graph(root_graph)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.client.close()

    @property
    def root_graph(self) -> Graph:
        return self.get_graph(URIRef(self.root_profile_iri))

    @property
    def modules_graph(self) -> Graph:
        return self.get_graph(URIRef(PYLODE_CONFIG_GRAPH))

    def load_remote_resources(self, graph: Graph, remote_resources: list[str], load_into_graph: bool = False):
        for remote_resource in remote_resources:
            logger.debug(f"Fetching remote resource {remote_resource}")
            _graph = Graph()
            try:
                _graph.parse(str(remote_resource))
            except Exception as err:
                raise RuntimeError(f"Failed to parse data from {remote_resource}. {err}")

            # named_graph = _graph.value(None, RDF.type, PROF.Profile)
            # remote_graph = Graph(identifier=named_graph)
            # remote_graph.__iadd__(_graph)

            if _graph.value(predicate=RDF.type, object=PROF.Profile):
                # TODO: Only the profile should be added to the new graph. The rest needs to go into the existing graph.
                for profile in _graph.subjects(RDF.type, PROF.Profile):
                    logger.debug(f"--- 🟢 adding new named graph {profile}")
                    cbd = Graph(identifier=str(profile)).__iadd__(_graph.cbd(profile))
                    self.add_graph(cbd)
                    other = Graph(identifier=graph.identifier).__iadd__(_graph - cbd)
                    graph.__iadd__(other)

                    # self.load_owl_imports(cbd)
                    # self.load_prof_resources(cbd)
                    # self.load_owl_imports(other)
                    # self.load_prof_resources(other)
            elif load_into_graph:
                logger.debug(f"--- 🟡 adding to existing graph {graph.identifier}")
                graph.__iadd__(_graph)
                logger.debug(f"--- 🟢 adding new named graph {remote_resource}")
                _graph = Graph(identifier=graph.identifier).__iadd__(_graph)
                self.load_owl_imports(_graph)
                self.load_prof_resources(_graph)
            else:
                logger.debug(f"--- 🟡 adding to existing graph {graph.identifier}")
                graph.__iadd__(_graph)

    def load_owl_imports2(self, graph: Graph):
        import_values = [str(v) for v in graph.objects(None, OWL.imports)]
        if import_values:
            logger.debug(f"📂 Found the owl:imports values {import_values}")
        self.load_remote_resources(graph, import_values)

    def load_prof_resources(self, graph: Graph):
        prof_resource_descriptors = graph.objects(None, PROF.hasResource)
        remote_resources = []
        for prof_resource_descriptor in prof_resource_descriptors:
            mediatype = graph.value(prof_resource_descriptor, DCTERMS.format)
            # TODO: currently only supports RDF Turtle.
            if mediatype is not None and str(mediatype) == "text/turtle":
                remote_resource = graph.value(prof_resource_descriptor, PROF.hasArtifact)
                if remote_resource is not None:
                    remote_resources.append(str(remote_resource))

        if remote_resources:
            logger.debug(f"📂 Found PROF resource values {remote_resources}")

        self.load_remote_resources(graph, remote_resources, True)


def load_profiles(root_profile_iri: str, data: str) -> Dataset:
    """Create an RDF Dataset by expanding the initial profile data.

        Profiles are mapped to a named graph in the dataset.
        This function loads statements from owl:imports and PROF resources.
        These statements are loaded into their profile's named graphs.
    """
    starttime = time.time()
    with ProfilesDataset(root_profile_iri, data) as db:
        logger.debug(f"Finished loading profiles in {time.time() - starttime:.2f} seconds.")
        logger.debug(f"Named graphs:")
        logger.debug([str(g.identifier) for g in db.graphs()])
        logger.debug(f"pylode config:\n{db.get_graph(URIRef(PYLODE_CONFIG_GRAPH)).serialize(format='longturtle')}")
        db.serialize("output.trig", format="nquads")

        return db