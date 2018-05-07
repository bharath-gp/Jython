import datetime

from bucket_utils.bucket_ready_functions import bucket_utils
from cbas.cbas_base import CBASBaseTest
from couchbase_helper.documentgenerator import DocumentGenerator
from memcached.helper.data_helper import MemcachedClientHelper
from remote.remote_util import RemoteMachineShellConnection


class CBASBugAutomation(CBASBaseTest):

    def setUp(self):
        # Invoke CBAS setUp method
        super(CBASBugAutomation, self).setUp()  

    @staticmethod
    def generate_documents(start_at, end_at):
        age = range(70)
        first = ['james', 'sharon', 'dave', 'bill', 'mike', 'steve']
        profession = ['doctor', 'lawyer']
        template = '{{ "number": {0}, "first_name": "{1}" , "profession":"{2}", "mutated":0}}'
        documents = DocumentGenerator('test_docs', template, age, first, profession, start=start_at, end=end_at)
        return documents

    def test_multiple_cbas_data_set_creation(self):

        '''
        -i b/resources/4-nodes-template.ini -t cbas.cbas_bug_automation.CBASBugAutomation.test_multiple_cbas_data_set_creation,default_bucket=False,
        sample_bucket_docs_count=31591,cb_bucket_name=travel-sample,num_of_datasets=8,where_field=country,where_value=United%States:France:United%Kingdom
        '''

        self.log.info("Load sample bucket")
        couchbase_bucket_docs_count = self.input.param("sample_bucket_docs_count", self.travel_sample_docs_count)
        couchbase_bucket_name = self.input.param("cb_bucket_name", self.cb_bucket_name)
        result = self.load_sample_buckets(servers=list([self.master]),
                                          bucketName=couchbase_bucket_name,
                                          total_items=couchbase_bucket_docs_count)
        self.assertTrue(result, "Failed to load sample bucket")

        self.log.info("Create connection")
        self.cbas_util.createConn(couchbase_bucket_name)

        self.log.info("Create a CBAS bucket")
        self.cbas_util.create_bucket_on_cbas(cbas_bucket_name=self.cbas_bucket_name, # default value for cbas_bucket_name is configured inside the cbas_base
                                             cb_bucket_name=couchbase_bucket_name)
        
        '''
        If where value contains space replace it with %, inside test replace the % with space. 
        Also, if we have more than 1 value delimit them using :
        '''
        field = self.input.param("where_field", "")
        values = self.input.param("where_value", "")
        if values:
            values = values.replace("%", " ").split(":")

        self.log.info("Create data-sets")
        num_of_datasets = self.input.param("num_of_datasets", 8)
        for index in range(num_of_datasets):
            if index < len(values) and field and values:
                self.cbas_util.create_dataset_on_bucket(cbas_bucket_name=self.cbas_bucket_name,
                                                        cbas_dataset_name=self.cbas_dataset_name + str(index),
                                                        where_field=field, where_value=values[index])
            else:
                self.cbas_util.create_dataset_on_bucket(cbas_bucket_name=self.cbas_bucket_name,
                                                        cbas_dataset_name=self.cbas_dataset_name + str(index))

        self.log.info("Connect to CBAS bucket")
        self.cbas_util.connect_to_bucket(cbas_bucket_name=self.cbas_bucket_name,
                                         cb_bucket_password=self.cb_bucket_password)

        self.log.info("Wait for ingestion to completed and assert count")
        for index in range(num_of_datasets):
            if index < len(values) and field and values:
                count_n1ql = self.rest.query_tool('select count(*) from `%s` where %s = "%s"' % (self.cb_bucket_name, field, values[index]))['results'][0]['$1']
            else:
                count_n1ql = self.rest.query_tool('select count(*) from `%s`' % self.cb_bucket_name)['results'][0]['$1']
            self.cbas_util.wait_for_ingestion_complete([self.cbas_dataset_name + str(index)], count_n1ql)
            _, _, _, results, _ = self.cbas_util.execute_statement_on_cbas_util('select count(*) from `%s`' % (self.cbas_dataset_name + str(index)))
            count_ds = results[0]["$1"]
            self.assertEqual(count_ds, count_n1ql, msg="result count mismatch between N1QL and Analytics")

    def test_cbas_queries_in_parallel_with_data_ingestion_on_multiple_cb_buckets(self):

        '''
        -i b/resources/4-nodes-template.ini -t cbas.cbas_bug_automation.CBASBugAutomation.test_cbas_queries_in_parallel_with_data_ingestion_on_multiple_cb_buckets,
        default_bucket=False,num_of_cb_buckets=4,items=1000,minutes_to_run=1
        '''

        self.log.info("Get the available memory quota")
        bucket_util = bucket_utils(self.master)
        self.info = bucket_util.rest.get_nodes_self()
        threadhold_memory = 1024
        total_memory_in_mb = self.info.memoryTotal / 1024 ** 2
        total_available_memory_in_mb = total_memory_in_mb
        active_service = self.info.services

        if "index" in active_service:
            total_available_memory_in_mb -= self.info.indexMemoryQuota
        if "fts" in active_service:
            total_available_memory_in_mb -= self.info.ftsMemoryQuota
        if "cbas" in active_service:
            total_available_memory_in_mb -= self.info.cbasMemoryQuota
        if "eventing" in active_service:
            total_available_memory_in_mb -= self.info.eventingMemoryQuota
        
        print(total_memory_in_mb)
        available_memory =  total_available_memory_in_mb - threadhold_memory 
        self.rest.set_service_memoryQuota(service='memoryQuota', memoryQuota=available_memory)

        self.log.info("Add a KV nodes")
        result = self.add_node(self.servers[1], services=["kv"], rebalance=False)
        self.assertTrue(result, msg="Failed to add KV node.")

        self.log.info("Add a CBAS nodes")
        result = self.add_node(self.cbas_servers[0], services=["cbas"], rebalance=True)
        self.assertTrue(result, msg="Failed to add CBAS node.")

        self.log.info("Create CB buckets")
        num_of_cb_buckets = self.input.param("num_of_cb_buckets", 4)
        for i in range(num_of_cb_buckets):
            self.create_bucket(self.master, "default" + str(i), bucket_ram=(available_memory / num_of_cb_buckets))

        self.log.info("Create connections for CB buckets")
        for i in range(num_of_cb_buckets):
            self.cbas_util.createConn("default" + str(i))

        self.log.info("Create CBAS buckets")
        for i in range(num_of_cb_buckets):
            self.cbas_util.create_bucket_on_cbas(cbas_bucket_name="cbas_default" + str(i),
                                                 cb_bucket_name="default" + str(i))

        self.log.info("Create data-sets")
        for i in range(num_of_cb_buckets):
            self.cbas_util.create_dataset_on_bucket(cbas_bucket_name="cbas_default" + str(i),
                                                    cbas_dataset_name="cbas_default_ds" + str(i))

        self.log.info("Connect to CBAS buckets")
        for i in range(num_of_cb_buckets):
            result = self.cbas_util.connect_to_bucket(cbas_bucket_name="cbas_default" + str(i),
                                                      cb_bucket_password=self.cb_bucket_password)
            self.assertTrue(result, msg="Failed to connect cbas bucket")

        self.log.info("Generate documents")
        num_of_documents_per_insert_update = self.input.param("items", 1000)
        load_gen = CBASBugAutomation.generate_documents(0, num_of_documents_per_insert_update)

        self.log.info("Asynchronously insert documents in CB buckets")
        tasks = self._async_load_all_buckets(server=self.master, kv_gen=load_gen, op_type="create", exp=0, batch_size=100)
        for task in tasks:
            self.log.info(task.get_result())

        self.log.info("Asynchronously create/update documents in CB buckets")
        start_insert_update_from = num_of_documents_per_insert_update
        end_insert_update_at = start_insert_update_from + num_of_documents_per_insert_update
        minutes_to_run = self.input.param("minutes_to_run", 5)
        end_time = datetime.datetime.now() + datetime.timedelta(minutes=int(minutes_to_run))
        while datetime.datetime.now() < end_time:
            try:
                self.log.info("start creation of new documents")
                load_gen = CBASBugAutomation.generate_documents(start_insert_update_from, end_insert_update_at)
                tasks = self._async_load_all_buckets(server=self.master, kv_gen=load_gen, op_type="create", exp=0, batch_size=100)
                for task in tasks:
                    self.log.info(task.get_result())

                self.log.info("start updating of documents created in the last iteration")
                load_previous_iteration_gen = CBASBugAutomation.generate_documents(start_insert_update_from - num_of_documents_per_insert_update, end_insert_update_at - num_of_documents_per_insert_update)
                tasks = self._async_load_all_buckets(server=self.master, kv_gen=load_previous_iteration_gen, op_type="update", exp=0, batch_size=100)
                for task in tasks:
                    self.log.info(task.get_result())

                start_insert_update_from = end_insert_update_at
                end_insert_update_at = end_insert_update_at + num_of_documents_per_insert_update

            except:
                pass

            for i in range(num_of_cb_buckets):
                try:
                    self.cbas_util.execute_statement_on_cbas_util('select count(*) from `%s`' % ("cbas_default_ds" + str(i)))
                    self.cbas_util.execute_statement_on_cbas_util('select * from `%s`' % ("cbas_default_ds" + str(i)))
                except Exception as e:
                    self.log.info(str(e))

        self.log.info("Assert document count in CBAS dataset")
        for i in range(num_of_cb_buckets):
            count_n1ql = self.rest.query_tool('select count(*) from `%s`' % ("default" + str(i)))['results'][0]['$1']
            result = self.cbas_util.validate_cbas_dataset_items_count(dataset_name="cbas_default_ds" + str(i), expected_count=count_n1ql, expected_mutated_count=count_n1ql-num_of_documents_per_insert_update)

    '''
    cbas.cbas_bug_automation.CBASBugAutomation.test_analytics_queries_using_cbq,cb_bucket_name=default,items=1000,cbas_bucket_name=default_cbas,cbas_dataset_name=ds,cb_bucket_name=default
    '''

    def test_analytics_queries_using_cbq(self):

        self.log.info("Load data in the default bucket")
        self.perform_doc_ops_in_all_cb_buckets(self.num_items, "create", 0, self.num_items, exp=0, batch_size=10)

        self.log.info("Create primary index")
        query = "CREATE PRIMARY INDEX ON {0} using gsi".format(self.cb_bucket_name)
        self.rest.query_tool(query)

        self.log.info("Create a connection")
        self.cbas_util.createConn(self.cb_bucket_name)

        self.log.info("Create a CBAS bucket")
        query = 'create bucket %s with {"name":"%s"}' % (self.cbas_bucket_name, self.cb_bucket_name)
        result = self.analytics_helper.run_commands_using_cbq_shell(query, self.cbas_node, 8095)
        self.assertTrue(result['status'] == "success", "Query %s failed." % query)

        self.log.info("Create a default data-set")
        query = 'create dataset %s on default_cbas' % self.cbas_dataset_name
        result = self.analytics_helper.run_commands_using_cbq_shell(query, self.cbas_node, 8095)
        self.assertTrue(result['status'] == "success", "Query %s failed." % query)

        self.log.info("Connect to cbas bucket")
        query = 'connect bucket %s' % self.cbas_bucket_name
        result = self.analytics_helper.run_commands_using_cbq_shell(query, self.cbas_node, 8095)
        self.assertTrue(result['status'] == "success", "Query %s failed." % query)

        self.log.info("Fetch dataset count")
        query = 'select count(*) from %s' % self.cbas_dataset_name
        result = self.analytics_helper.run_commands_using_cbq_shell(query, self.cbas_node, 8095)
        self.assertTrue(result['results'][0]['$1'] == self.num_items,
                        "Number of items incorrect %s != %s" % (result["results"][0], self.num_items))

        self.log.info("Disconnect cbas bucket")
        query = 'disconnect bucket %s' % self.cbas_bucket_name
        result = self.analytics_helper.run_commands_using_cbq_shell(query, self.cbas_node, 8095)
        self.assertTrue(result['status'] == "success", "Query %s failed." % query)

        self.log.info("Drop a dataset")
        query = 'drop dataset %s' % self.cbas_dataset_name
        result = self.analytics_helper.run_commands_using_cbq_shell(query, self.cbas_node, 8095)
        self.assertTrue(result['status'] == "success", "Query %s failed." % query)

        self.log.info("Drop bucket")
        query = 'drop bucket %s' % self.cbas_bucket_name
        result = self.analytics_helper.run_commands_using_cbq_shell(query, self.cbas_node, 8095)
        self.assertTrue(result['status'] == "success", "Query %s failed." % query)
    
    '''
    cbas.cbas_bug_automation.CBASBugAutomation.test_partial_rollback_via_memcached_restart_and_persistance_stopped,cb_bucket_name=default,items=10,cbas_bucket_name=default_cbas,cbas_dataset_name=ds,number_of_times_memcached_restart=16
    '''
    def test_partial_rollback_via_memcached_restart_and_persistance_stopped(self):
        self.log.info("Load data in the default bucket")
        self.perform_doc_ops_in_all_cb_buckets(self.num_items, "create", 0, self.num_items, exp=0, batch_size=10)
        
        self.log.info("Create connection")
        self.cbas_util.createConn(self.cb_bucket_name)
        
        self.log.info("Create bucket on CBAS")
        self.cbas_util.create_bucket_on_cbas(cbas_bucket_name=self.cbas_bucket_name,
                                             cb_bucket_name=self.cb_bucket_name,
                                             cb_server_ip=self.cb_server_ip)

        self.log.info("Create dataset on the CBAS bucket")
        self.cbas_util.create_dataset_on_bucket(cbas_bucket_name=self.cbas_bucket_name,
                                                cbas_dataset_name=self.cbas_dataset_name)

        self.log.info("Connect to Bucket")
        self.cbas_util.connect_to_bucket(cbas_bucket_name=self.cbas_bucket_name,
                                         cb_bucket_password=self.cb_bucket_password)
        
        self.log.info("Validate count on CBAS")
        self.cbas_util.validate_cbas_dataset_items_count(self.cbas_dataset_name, self.num_items)
        
        self.log.info("Establish remote shell to master node")
        shell = RemoteMachineShellConnection(self.master)
        
        number_of_times_memcached_restart = self.input.param("number_of_times_memcached_restart", 16)
        for i in range(number_of_times_memcached_restart):
            
            self.log.info("Stop persistance on KV node")
            mem_client = MemcachedClientHelper.direct_client(self.master,
                                                         self.cb_bucket_name)
            mem_client.stop_persistence()
            
            self.log.info("Add documents with persistance stopped")
            self.perform_doc_ops_in_all_cb_buckets(self.num_items / 2, "create", self.num_items, self.num_items + (self.num_items / 2), exp=0)
            
            self.log.info("Validate count on CBAS")
            self.cbas_util.validate_cbas_dataset_items_count(self.cbas_dataset_name, self.num_items+ (self.num_items / 2))
            
            self.log.info("Kill memcached on KV node %s" %str(i))
            shell.kill_memcached()
            self.sleep(10, "Wait for for DCP rollback sent to CBAS and memcached restart")
            
            self.log.info("Validate count on CBAS")
            self.assertTrue(self.cbas_util.validate_cbas_dataset_items_count(self.cbas_dataset_name, self.num_items), msg="Count mismatch")
            
    def tearDown(self):
        super(CBASBugAutomation, self).tearDown()
